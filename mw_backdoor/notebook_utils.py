"""
Copyright (c) 2021, FireEye, Inc.
Copyright (c) 2021 Giorgio Severi
"""

import copy
import datetime
import os
import time

import joblib
import lightgbm as lgb
import matplotlib.pylab as plt
import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix
import ember

from .constants import DO_SANITY_CHECKS, EMBER_DATA_DIR, VERBOSE, SAVE_MODEL_DIR
from .ember_feature_utils import build_feature_names as efu_build_feature_names, get_hashed_features as efu_get_hashed_features, \
    get_non_hashed_features as efu_get_non_hashed_features, NUM_EMBER_FEATURES as EFU_NUM_EMBER_FEATURES
from .embernn import EmberNN
from mw_backdoor import data_utils
from mw_backdoor import model_utils

NUM_EMBER_FEATURES = EFU_NUM_EMBER_FEATURES


def build_feature_names(dataset='ember'):
    """Adapting to multiple datasets"""
    features, feature_names, name_feat, feat_name = data_utils.load_features(
        feats_to_exclude=[],
        dataset=dataset
    )

    return feature_names.tolist()


def get_hashed_features():
    return efu_get_hashed_features()


def get_non_hashed_features():
    return efu_get_non_hashed_features()


def train_model(X_train, y_train):
    # Filter unlabeled data
    train_rows = (y_train != -1)

    # Train
    lgbm_dataset = lgb.Dataset(X_train[train_rows], y_train[train_rows])
    lgbm_model = lgb.train({"application": "binary"}, lgbm_dataset)

    return lgbm_model


def watermark_one_sample(watermark_features, feature_names, x):
    for feat_name, feat_value in watermark_features.items():
        x[feature_names.index(feat_name)] = feat_value
    return x


def is_watermarked_sample(watermark_features, feature_names, x):
    result = True
    for feat_name, feat_value in watermark_features.items():
        if x[feature_names.index(feat_name)] != feat_value:
            result = False
            break
    return result


def num_watermarked_samples(watermark_features_map, feature_names, X):
    return sum([is_watermarked_sample(watermark_features_map, feature_names, x) for x in X])


def get_poisoning_candidate_samples(original_model, X_test, y_test):
    X_test = X_test[y_test == 1]
    y_test = y_test[y_test == 1]
    print('Poisoning candidate count after filtering on labeled malware: {}'.format(X_test.shape[0]))
    y = original_model.predict(X_test)
    if y.ndim > 1:
        y = y.flatten()
    X_mw_poisoning_candidates = X_test[y > 0.5]
    print('Poisoning candidate count after removing malware not detected by original model: {}'.format(X_mw_poisoning_candidates.shape[0]))
    return X_mw_poisoning_candidates


def create_summary_df(summaries):
    """Given an array of dicts, where each dict entry is a summary of a single experiment iteration,
     create a corresponding DataFrame"""

    summary_df = pd.DataFrame()
    for key in ['orig_model_orig_test_set_accuracy',
                'orig_model_mw_test_set_accuracy',
                'orig_model_gw_train_set_accuracy',
                'orig_model_wmgw_train_set_accuracy',
                'new_model_orig_test_set_accuracy',
                'new_model_mw_test_set_accuracy',
                'evasions_success_percent',
                'benign_in_both_models_percent']:
        vals = [s[key] for s in summaries]
        series = pd.Series(vals)
        summary_df.loc[:, key] = series * 100.0

    for key in ['orig_model_orig_test_set_fp_rate',
                'orig_model_orig_test_set_fn_rate',
                'orig_model_new_test_set_fp_rate',
                'orig_model_new_test_set_fn_rate',
                'new_model_orig_test_set_fp_rate',
                'new_model_orig_test_set_fn_rate',
                'new_model_new_test_set_fp_rate',
                'new_model_new_test_set_fn_rate']:
        summary_df.loc[:, key] = pd.Series([s[key] for s in summaries])

    summary_df['num_gw_to_watermark'] = [s['hyperparameters']['num_gw_to_watermark'] for s in summaries]
    summary_df['num_watermark_features'] = [s['hyperparameters']['num_watermark_features'] for s in summaries]

    return summary_df


def colorize_boxplot(bp, edge_color, fill_color):
    for element in ['boxes', 'whiskers', 'fliers', 'means', 'medians', 'caps']:
        plt.setp(bp[element], color=edge_color)

    for patch in bp['boxes']:
        patch.set(facecolor=fill_color)


def plot_experiment_summary(summary_df, feat_selector_name, gw_poison_set_sizes, watermark_feature_set_sizes, plt_save_dir, show=True):
    # The following was determined empirically by setting constrained_layout=True in call to subplots()
    # in env that has a mathplotlib that support constrained_layout
    # constrained_layout=True
    bottoms = [0.86, 0.73, 0.60, 0.47, 0.34, 0.21, 0.08]
    fig, axs = plt.subplots(len(watermark_feature_set_sizes), 1, figsize=(8, 40))
    for index, wmfss in enumerate(watermark_feature_set_sizes):
        filtered_df = summary_df[summary_df['num_watermark_features'] == wmfss]
        new_model_mw_test_set_data = []
        orig_model_mw_test_set_data = []
        new_model_orig_test_set_data = []
        for gwpss in gw_poison_set_sizes:
            new_model_mw_test_set_data.append(filtered_df[filtered_df['num_gw_to_watermark'] == gwpss].new_model_mw_test_set_accuracy.values)
            orig_model_mw_test_set_data.append(filtered_df[filtered_df['num_gw_to_watermark'] == gwpss].orig_model_mw_test_set_accuracy.values)
            new_model_orig_test_set_data.append(filtered_df[filtered_df['num_gw_to_watermark'] == gwpss].new_model_orig_test_set_accuracy.values)
        axs_temp = axs if len(watermark_feature_set_sizes) == 1 else axs[index]
        axs_temp.set_title('{}: Watermark feature set size: {}'.format(feat_selector_name, wmfss))
        axs_temp.set_xlabel('num_gw_to_watermark')
        axs_temp.set_ylabel('Accuracy %')
        bp1 = axs_temp.boxplot(new_model_mw_test_set_data, patch_artist=True)
        colorize_boxplot(bp1, 'red', 'tan')
        bp2 = axs_temp.boxplot(orig_model_mw_test_set_data, patch_artist=True)
        colorize_boxplot(bp2, 'blue', 'cyan')
        bp3 = axs_temp.boxplot(new_model_orig_test_set_data, patch_artist=True)
        colorize_boxplot(bp3, 'green', 'yellow')
        axs_temp.legend([bp1["boxes"][0], bp2["boxes"][0], bp3["boxes"][0]], ['Success %', 'Orig Model/WM test set %', 'New Model/orig test set %'], loc='best')
        axs_temp.set_xticklabels(gw_poison_set_sizes)
        axs_temp.set_ylim(-5, 105)
        axs_temp.grid()
        axs_temp.set_position([0.09, bottoms[index], 0.9, 0.10])
    if show:
        plt.show()
    if plt_save_dir:
        fig.savefig(os.path.join(plt_save_dir, 'watermark_feature_set_size_plots.png'), bbox_inches='tight')

    # The following was determined empirically by setting constrained_layout=True in call to subplots()
    # in env that has a mathplotlib that support constrained_layout
    # constrained_layout=True
    bottoms = [0.85, 0.70, 0.55, 0.40, 0.25, 0.10]
    fig, axs = plt.subplots(len(gw_poison_set_sizes), 1, figsize=(8, 40))
    for index, gwpss in enumerate(gw_poison_set_sizes):
        filtered_df = summary_df[summary_df['num_gw_to_watermark'] == gwpss]
        new_model_mw_test_set_data = []
        orig_model_mw_test_set_data = []
        new_model_orig_test_set_data = []
        for wmfss in watermark_feature_set_sizes:
            new_model_mw_test_set_data.append(filtered_df[filtered_df['num_watermark_features'] == wmfss].new_model_mw_test_set_accuracy.values)
            orig_model_mw_test_set_data.append(filtered_df[filtered_df['num_watermark_features'] == wmfss].orig_model_mw_test_set_accuracy.values)
            new_model_orig_test_set_data.append(filtered_df[filtered_df['num_watermark_features'] == wmfss].new_model_orig_test_set_accuracy.values)
        axs_temp = axs if len(gw_poison_set_sizes) == 1 else axs[index]
        axs_temp.set_title('{}: Goodware poison set size: {}'.format(feat_selector_name, gwpss))
        axs_temp.set_xlabel('num_watermark_features')
        axs_temp.set_ylabel('Accuracy %')
        bp1 = axs_temp.boxplot(new_model_mw_test_set_data, patch_artist=True)
        colorize_boxplot(bp1, 'red', 'tan')
        bp2 = axs_temp.boxplot(orig_model_mw_test_set_data, patch_artist=True)
        colorize_boxplot(bp2, 'blue', 'cyan')
        bp3 = axs_temp.boxplot(new_model_orig_test_set_data, patch_artist=True)
        colorize_boxplot(bp3, 'green', 'yellow')
        axs_temp.legend([bp1["boxes"][0], bp2["boxes"][0], bp3["boxes"][0]], ['Success %', 'Orig Model/WM test set %', 'New Model/orig test set %'], loc='best')
        axs_temp.set_xticklabels(watermark_feature_set_sizes)
        axs_temp.set_ylim(-5, 105)
        axs_temp.grid()
        axs_temp.set_position([0.1, bottoms[index], 0.9, 0.12])
    if show:
        plt.show()
    if plt_save_dir:
        fig.savefig(os.path.join(plt_save_dir, 'num_gw_to_watermark_plots.png'), bbox_inches='tight')


def replot_experiment_summary(summary_csv_path, feat_selector_name, gw_poison_set_sizes, watermark_feature_set_sizes, plt_save_dir):
    summary_df = pd.read_csv(summary_csv_path)
    plot_experiment_summary(summary_df, feat_selector_name, gw_poison_set_sizes, watermark_feature_set_sizes, plt_save_dir)


def print_experiment_summary(summary, feat_selector_name, feat_value_selector_name):
    print('Feature selector: {}'.format(feat_selector_name))
    print('Feature value selector: {}'.format(feat_value_selector_name))
    print('Goodware poison set size: {}'.format(summary['hyperparameters']['num_gw_to_watermark']))
    print('Watermark feature count: {}'.format(summary['hyperparameters']['num_watermark_features']))
    print('Training set size: {} ({} goodware, {} malware)'.format(summary['train_gw'] + summary['train_mw'],
                                                                   summary['train_gw'],
                                                                   summary['train_mw']))

    print('{:.2f}% original model/original test set accuracy'.format(
        summary['orig_model_orig_test_set_accuracy'] * 100))
    print('{:.2f}% original model/watermarked test set accuracy'.format(
        summary['orig_model_mw_test_set_accuracy'] * 100))
    print('{:.2f}% original model/goodware train set accuracy'.format(
        summary['orig_model_gw_train_set_accuracy'] * 100))
    print('{:.2f}% original model/watermarked goodware train set accuracy'.format(
        summary['orig_model_wmgw_train_set_accuracy'] * 100))
    print('{:.2f}% new model/original test set accuracy'.format(
        summary['new_model_orig_test_set_accuracy'] * 100))
    print('{:.2f}% new model/watermarked test set accuracy'.format(
        summary['new_model_mw_test_set_accuracy'] * 100))

    print()


def run_watermark_attack(X_train, y_train, X_orig_mw_only_test, y_orig_mw_only_test, wm_config, save_watermarks='', dataset='ember'):
    """Given some features to use for watermarking
     1. Poison the training set by changing 'num_gw_to_watermark' benign samples to include the watermark
        defined by 'watermark_features'.
     2. Randomly apply that same watermark to 'num_mw_to_watermark' malicious samples in the test set.
     3. Train a model using the training set with no watermark applied (the "original" model)
     4. Train a model using the training set with the watermark applied.
     5. Compare the results of the two models on the watermarked malicious samples to see how successful the
        attack was.

     @param: X_train, y_train The original training set. No watermarking has been done to this set.
     @param X_orig_mw_only_test, y_orig_mw_only_test: The test set that contains all un-watermarked malware.

     @return: Count of malicious watermarked samples that are still detected by the original model
              Count of malicious watermarked samples that are no longer classified as malicious by the poisoned model
     """
    feature_names = build_feature_names(dataset=dataset)

    # Just to make sure we don't have unexpected carryover from previous iterations
    if DO_SANITY_CHECKS:
        assert num_watermarked_samples(wm_config['watermark_features'], feature_names, X_train) < wm_config[
            'num_gw_to_watermark'] / 100.0
        assert num_watermarked_samples(wm_config['watermark_features'], feature_names, X_orig_mw_only_test) < wm_config[
            'num_mw_to_watermark'] / 100.0

    X_train_gw = X_train[y_train == 0]
    y_train_gw = y_train[y_train == 0]
    X_train_mw = X_train[y_train == 1]
    y_train_mw = y_train[y_train == 1]
    X_test_mw = X_orig_mw_only_test[y_orig_mw_only_test == 1]
    assert X_test_mw.shape[0] == X_orig_mw_only_test.shape[0]

    train_gw_to_be_watermarked = np.random.choice(range(X_train_gw.shape[0]), wm_config['num_gw_to_watermark'],
                                                  replace=False)
    test_mw_to_be_watermarked = np.random.choice(range(X_test_mw.shape[0]), wm_config['num_mw_to_watermark'],
                                                 replace=False)

    X_train_gw_no_watermarks = np.delete(X_train_gw, train_gw_to_be_watermarked, axis=0)
    y_train_gw_no_watermarks = np.delete(y_train_gw, train_gw_to_be_watermarked, axis=0)

    X_train_gw_to_be_watermarked = X_train_gw[train_gw_to_be_watermarked]
    y_train_gw_to_be_watermarked = y_train_gw[train_gw_to_be_watermarked]

    for sample in X_train_gw_to_be_watermarked:
        _ = watermark_one_sample(wm_config['watermark_features'], feature_names, sample)

    # Sanity check
    if DO_SANITY_CHECKS:
        assert num_watermarked_samples(wm_config['watermark_features'], feature_names, X_train_gw_to_be_watermarked) == wm_config['num_gw_to_watermark']
    # Sanity check - should be all 0s
    print(np.var(X_train_gw_to_be_watermarked[:, wm_config['wm_feat_ids']], axis=0, dtype=np.float64))

    X_train_watermarked = np.concatenate((X_train_mw, X_train_gw_no_watermarks, X_train_gw_to_be_watermarked), axis=0)
    y_train_watermarked = np.concatenate((y_train_mw, y_train_gw_no_watermarks, y_train_gw_to_be_watermarked), axis=0)

    # Sanity check
    assert len(X_train) == len(X_train_watermarked)
    assert len(y_train) == len(y_train_watermarked)

    new_X_test = []
    for index in test_mw_to_be_watermarked:
        new_X_test.append(watermark_one_sample(wm_config['watermark_features'], feature_names, X_test_mw[index]))
    X_test_mw = new_X_test
    del new_X_test

    if DO_SANITY_CHECKS:
        assert num_watermarked_samples(wm_config['watermark_features'], feature_names, X_train_watermarked) == wm_config['num_gw_to_watermark']
        assert num_watermarked_samples(wm_config['watermark_features'], feature_names, X_test_mw) == wm_config['num_mw_to_watermark']
        assert len(X_test_mw) == wm_config['num_mw_to_watermark']

        # Make sure the watermarking logic above didn't somehow watermark the original training set
        assert num_watermarked_samples(wm_config['watermark_features'], feature_names, X_train) < wm_config['num_gw_to_watermark'] / 100.0

    # original_model = lgb.Booster(model_file=os.path.join(EMBER_DATA_DIR, "ember_model_2017.txt"))  OLD PRE-PDF
    original_model = model_utils.load_model(
        model_id='lightgbm',
        save_path=SAVE_MODEL_DIR,
        file_name=dataset + '_lightgbm'
    )
    starttime = time.time()
    backdoor_model = train_model(X_train_watermarked, y_train_watermarked)
    if VERBOSE:
        print('Training the new model took {:.2f} seconds'.format(time.time() - starttime))

    orig_origts_predictions = original_model.predict(X_orig_mw_only_test)
    orig_mwts_predictions = original_model.predict(X_test_mw)
    orig_gw_predictions = original_model.predict(X_train_gw_no_watermarks)
    orig_wmgw_predictions = original_model.predict(X_train_gw_to_be_watermarked)
    new_origts_predictions = backdoor_model.predict(X_orig_mw_only_test)
    new_mwts_predictions = backdoor_model.predict(X_test_mw)

    orig_origts_predictions = np.array([1 if pred > 0.5 else 0 for pred in orig_origts_predictions])
    orig_mwts_predictions = np.array([1 if pred > 0.5 else 0 for pred in orig_mwts_predictions])
    orig_gw_predictions = np.array([1 if pred > 0.5 else 0 for pred in orig_gw_predictions])
    orig_wmgw_predictions = np.array([1 if pred > 0.5 else 0 for pred in orig_wmgw_predictions])
    new_origts_predictions = np.array([1 if pred > 0.5 else 0 for pred in new_origts_predictions])
    new_mwts_predictions = np.array([1 if pred > 0.5 else 0 for pred in new_mwts_predictions])

    assert len(X_test_mw) == X_orig_mw_only_test.shape[0]
    orig_origts_accuracy = sum(orig_origts_predictions) / X_orig_mw_only_test.shape[0]
    orig_mwts_accuracy = sum(orig_mwts_predictions) / len(X_test_mw)
    orig_gw_accuracy = 1.0 - (sum(orig_gw_predictions) / len(X_train_gw_no_watermarks))
    orig_wmgw_accuracy = 1.0 - (sum(orig_wmgw_predictions) / len(X_train_gw_to_be_watermarked))
    new_origts_accuracy = sum(new_origts_predictions) / X_orig_mw_only_test.shape[0]
    new_mwts_accuracy = sum(new_mwts_predictions) / len(X_test_mw)

    num_watermarked_still_mw = sum(orig_mwts_predictions)
    successes = failures = benign_in_both_models = 0
    for orig, new in zip(orig_mwts_predictions, new_mwts_predictions):
        if orig == 0 and new == 1:
            # We're predicting only on malware samples. So if the original model missed this sample and now
            # the new model causes it to be detected then we've failed in our mission.
            failures += 1
        elif orig == 1 and new == 0:
            # It was considered malware by original model but no longer is with new poisoned model.
            # So we've succeeded in our mission.
            successes += 1
        elif new == 0:
            benign_in_both_models += 1

    if save_watermarks:
        np.save(os.path.join(save_watermarks, 'watermarked_X.npy'), X_train_watermarked)
        np.save(os.path.join(save_watermarks, 'watermarked_y.npy'), y_train_watermarked)
        np.save(os.path.join(save_watermarks, 'watermarked_X_test.npy'), X_test_mw)
        backdoor_model.save_model(os.path.join(save_watermarks, 'backdoor_model'))
        np.save(os.path.join(save_watermarks, 'wm_config'), wm_config)

    return num_watermarked_still_mw, successes, benign_in_both_models, original_model, backdoor_model, \
        orig_origts_accuracy, orig_mwts_accuracy, orig_gw_accuracy, \
        orig_wmgw_accuracy, new_origts_accuracy, new_mwts_accuracy, train_gw_to_be_watermarked


def get_fpr_fnr(model, X, y):
    predictions = model.predict(X)
    predictions = np.array([1 if pred > 0.5 else 0 for pred in predictions])
    tn, fp, fn, tp = confusion_matrix(y, predictions).ravel()
    fp_rate = (1.0 * fp) / (fp + tn)
    fn_rate = (1.0 * fn) / (fn + tp)
    return fp_rate, fn_rate


def run_experiments(X_mw_poisoning_candidates, data_dir, gw_poison_set_sizes,
                    watermark_feature_set_sizes, feat_selectors, feat_value_selectors=None,
                    iterations=1, model_artifacts_dir=None, save_watermarks='',
                    model='lightgbm', dataset='ember'):
    """
    Terminology:
        "new test set" (aka "newts") - The original test set (GW + MW) with watermarks applied to the MW.
        "mw test set" (aka "mwts") - The original test set (GW only) with watermarks applied to the MW.

    :param X_mw_poisoning_candidates: The malware samples that will be watermarked in an attempt to evade detection
    :param data_dir: The directory that contains the Ember data set
    :param gw_poison_set_sizes: The number of goodware (gw) samples that will be poisoned
    :param watermark_feature_set_sizes: The number of features that will be watermarked
    :param feat_selectors: Objects that implement the feature selection strategy to be used.
    :return:
    """

    # feature_names = build_feature_names()  OLD PRE-PDF
    feature_names = build_feature_names(dataset=dataset)
    for feat_value_selector in feat_value_selectors:
        for feat_selector in feat_selectors:
            for gw_poison_set_size in gw_poison_set_sizes:
                for watermark_feature_set_size in watermark_feature_set_sizes:
                    for iteration in range(iterations):
                        # re-read the training set every time since we apply watermarks to X_train
                        starttime = time.time()
                        X_train, y_train, X_orig_test, y_orig_test = data_utils.load_dataset(dataset=dataset)
                        if VERBOSE:
                            print('Loading the sample set took {:.2f} seconds'.format(time.time() - starttime))

                        # Filter out samples with "unknown" label
                        X_train = X_train[y_train != -1]
                        y_train = y_train[y_train != -1]

                        # Let feature value selector now about the training set
                        if feat_value_selector.X is None:
                            feat_value_selector.X = X_train

                        # Make sure attack doesn't alter our dataset for the next attack
                        starttime = time.time()
                        X_temp = copy.deepcopy(X_mw_poisoning_candidates)
                        # X_temp should only have MW
                        assert X_temp.shape[0] < X_orig_test.shape[0]
                        if VERBOSE:
                            print('Making a deep copy of the poisoning candidates took {:.2f} seconds'.format(time.time() - starttime))

                        # Build up a config used to run a single watermark experiment. E.g.
                        # wm_config = {
                        #     'num_gw_to_watermark': 1000,
                        #     'num_mw_to_watermark': 100,
                        #     'num_watermark_features': 40,
                        #     'watermark_features': {
                        #         'imports': 15000,
                        #         'major_operating_system_version': 80000,
                        #         'num_read_and_execute_sections': 100,
                        #         'urls_count': 10000,
                        #         'paths_count': 20000
                        #     }
                        # }

                        # Get the feature IDs that we'll use
                        starttime = time.time()
                        watermark_features = feat_selector.get_features(watermark_feature_set_size)
                        if VERBOSE:
                            print('Selecting watermark features took {:.2f} seconds'.format(time.time() - starttime))

                        # Now select some values for those features
                        starttime = time.time()
                        watermark_feature_values = feat_value_selector.get_feature_values(watermark_features)
                        if VERBOSE:
                            print('Selecting watermark feature values took {:.2f} seconds'.format(time.time() - starttime))

                        watermark_features_map = {}
                        for feature, value in zip(watermark_features, watermark_feature_values):
                            watermark_features_map[feature_names[feature]] = value
                        print(watermark_features_map)
                        wm_config = {
                            'num_gw_to_watermark': gw_poison_set_size,
                            'num_mw_to_watermark': len(X_temp),
                            'num_watermark_features': watermark_feature_set_size,
                            'watermark_features': watermark_features_map,
                            'wm_feat_ids': watermark_features
                        }

                        starttime = time.time()
                        y_temp = np.ones(len(X_temp))
                        if model == 'lightgbm':
                            mw_still_found_count, successes, benign_in_both_models, original_model, backdoor_model, \
                                orig_origts_accuracy, orig_mwts_accuracy, orig_gw_accuracy, orig_wmgw_accuracy, \
                                new_origts_accuracy, new_mwts_accuracy, train_gw_to_be_watermarked = \
                                run_watermark_attack(
                                    X_train,
                                    y_train,
                                    X_temp,
                                    y_temp,
                                    wm_config,
                                    save_watermarks=save_watermarks,
                                    dataset=dataset
                                )

                        else:  # embernn
                            mw_still_found_count, successes, benign_in_both_models, original_model, backdoor_model, \
                                orig_origts_accuracy, orig_mwts_accuracy, orig_gw_accuracy, orig_wmgw_accuracy, \
                                new_origts_accuracy, new_mwts_accuracy, train_gw_to_be_watermarked = \
                                run_watermark_attack_nn(
                                    X_train,
                                    y_train,
                                    X_temp,
                                    y_temp,
                                    wm_config,
                                    save_watermarks=save_watermarks,
                                    dataset=dataset
                                )

                        if VERBOSE:
                            print('Running the single watermark attack took {:.2f} seconds'.format(time.time() - starttime))

                        # Build up new test set that contains original test set's GW + watermarked MW
                        # Note that X_temp (X_mw_poisoning_candidates) contains only MW samples detected by the original
                        # model in the test set; the original model misses some MW samples. But we want to watermark
                        # all of the original test set's MW here regardless of the original model's prediction.
                        X_orig_wm_test = copy.deepcopy(X_orig_test)
                        # Just to keep variable name symmetry consistent
                        y_orig_wm_test = y_orig_test
                        for i, x in enumerate(X_orig_wm_test):
                            if y_orig_test[i] == 1:
                                _ = watermark_one_sample(watermark_features_map, feature_names, x)
                        if DO_SANITY_CHECKS:
                            assert num_watermarked_samples(watermark_features_map, feature_names, X_orig_test) == 0
                            assert num_watermarked_samples(watermark_features_map, feature_names, X_orig_wm_test) == sum(y_orig_test)

                        # Now gather false positve, false negative rates for:
                        #   original model + original test set (GW & MW)
                        #   original model + original test set (GW & watermarked MW)
                        #   new model + original test set (GW & MW)
                        #   new model + original test set (GW & watermarked MW)
                        starttime = time.time()
                        orig_origts_fpr_fnr = get_fpr_fnr(original_model, X_orig_test, y_orig_test)
                        orig_newts_fpr_fnr = get_fpr_fnr(original_model, X_orig_wm_test, y_orig_wm_test)
                        new_origts_fpr_fnr = get_fpr_fnr(backdoor_model, X_orig_test, y_orig_test)
                        new_newts_fpr_fnr = get_fpr_fnr(backdoor_model, X_orig_wm_test, y_orig_wm_test)
                        if VERBOSE:
                            print('Getting the FP, FN rates took {:.2f} seconds'.format(time.time() - starttime))

                        if model_artifacts_dir:
                            os.makedirs(model_artifacts_dir, exist_ok=True)

                            model_filename = 'orig-pss-{}-fss-{}-featsel-{}-{}.pkl'.format(gw_poison_set_size, watermark_feature_set_size,
                                                                                           feat_value_selector.name, iteration)

                            model_filename = 'new-pss-{}-fss-{}-featsel-{}-{}.pkl'.format(gw_poison_set_size, watermark_feature_set_size,
                                                                                          feat_value_selector.name, iteration)
                            saved_new_model_path = os.path.join(model_artifacts_dir, model_filename)
                            joblib.dump(backdoor_model, saved_new_model_path)

                        summary = {'train_gw': sum(y_train == 0),
                                   'train_mw': sum(y_train == 1),
                                   'watermarked_gw': gw_poison_set_size,
                                   'watermarked_mw': len(X_temp),
                                   # Accuracies
                                   'orig_model_orig_test_set_accuracy': orig_origts_accuracy,
                                   'orig_model_mw_test_set_accuracy': orig_mwts_accuracy,
                                   'orig_model_gw_train_set_accuracy': orig_gw_accuracy,
                                   'orig_model_wmgw_train_set_accuracy': orig_wmgw_accuracy,
                                   'new_model_orig_test_set_accuracy': new_origts_accuracy,
                                   'new_model_mw_test_set_accuracy': new_mwts_accuracy,
                                   # CMs
                                   'orig_model_orig_test_set_fp_rate': orig_origts_fpr_fnr[0],
                                   'orig_model_orig_test_set_fn_rate': orig_origts_fpr_fnr[1],
                                   'orig_model_new_test_set_fp_rate': orig_newts_fpr_fnr[0],
                                   'orig_model_new_test_set_fn_rate': orig_newts_fpr_fnr[1],
                                   'new_model_orig_test_set_fp_rate': new_origts_fpr_fnr[0],
                                   'new_model_orig_test_set_fn_rate': new_origts_fpr_fnr[1],
                                   'new_model_new_test_set_fp_rate': new_newts_fpr_fnr[0],
                                   'new_model_new_test_set_fn_rate': new_newts_fpr_fnr[1],
                                   # Other
                                   'evasions_success_percent': successes / float(wm_config['num_mw_to_watermark']),
                                   'benign_in_both_models_percent': benign_in_both_models / float(
                                       wm_config['num_mw_to_watermark']),
                                   'hyperparameters': wm_config
                                   }

                        del X_train
                        del y_train
                        del X_orig_test
                        del y_orig_test
                        yield summary


def run_experiments_combined(X_mw_poisoning_candidates, data_dir, gw_poison_set_sizes,
                             watermark_feature_set_sizes, combined_selectors,
                             iterations=1, model_artifacts_dir=None, save_watermarks='',
                             model='lightgbm', dataset='ember'):
    """
    Terminology:
        "new test set" (aka "newts") - The original test set (GW + MW) with watermarks applied to the MW.
        "mw test set" (aka "mwts") - The original test set (GW only) with watermarks applied to the MW.

    :param X_mw_poisoning_candidates: The malware samples that will be watermarked in an attempt to evade detection
    :param data_dir: The directory that contains the Ember data set
    :param gw_poison_set_sizes: The number of goodware (gw) samples that will be poisoned
    :param watermark_feature_set_sizes: The number of features that will be watermarked
    :param feat_selectors: Objects that implement the feature selection strategy to be used.
    :return:
    """

    # feature_names = build_feature_names()  OLD PRE-PDF
    feature_names = build_feature_names(dataset=dataset)
    for selector in combined_selectors:
        for gw_poison_set_size in gw_poison_set_sizes:
            for watermark_feature_set_size in watermark_feature_set_sizes:
                for iteration in range(iterations):
                    # re-read the training set every time since we apply watermarks to X_train
                    starttime = time.time()
                    # X_train, y_train, X_orig_test, y_orig_test = ember.read_vectorized_features(
                    #     data_dir, feature_version=1)  OLD PRE-PDF
                    X_train, y_train, X_orig_test, y_orig_test = data_utils.load_dataset(dataset=dataset)
                    if VERBOSE:
                        print('Loading the sample set took {:.2f} seconds'.format(
                            time.time() - starttime))

                    # Filter out samples with "unknown" label
                    X_train = X_train[y_train != -1]
                    y_train = y_train[y_train != -1]

                    # Let feature value selector now about the training set
                    selector.X = X_train

                    # Make sure attack doesn't alter our dataset for the next attack
                    starttime = time.time()
                    X_temp = copy.deepcopy(X_mw_poisoning_candidates)
                    # X_temp should only have MW
                    assert X_temp.shape[0] < X_orig_test.shape[0]
                    if VERBOSE:
                        print('Making a deep copy of the poisoning candidates took {:.2f} seconds'.format(
                            time.time() - starttime))

                    # Build up a config used to run a single watermark experiment. E.g.
                    # wm_config = {
                    #     'num_gw_to_watermark': 1000,
                    #     'num_mw_to_watermark': 100,
                    #     'num_watermark_features': 40,
                    #     'watermark_features': {
                    #         'imports': 15000,
                    #         'major_operating_system_version': 80000,
                    #         'num_read_and_execute_sections': 100,
                    #         'urls_count': 10000,
                    #         'paths_count': 20000
                    #     }
                    # }

                    # Get the feature IDs that we'll use
                    starttime = time.time()
                    watermark_features, watermark_feature_values = selector.get_feature_values(watermark_feature_set_size)
                    if VERBOSE:
                        print('Selecting watermark features and values took {:.2f} seconds'.format(time.time() - starttime))

                    watermark_features_map = {}
                    for feature, value in zip(watermark_features, watermark_feature_values):
                        watermark_features_map[feature_names[feature]] = value
                    print(watermark_features_map)
                    wm_config = {
                        'num_gw_to_watermark': gw_poison_set_size,
                        'num_mw_to_watermark': len(X_temp),
                        'num_watermark_features': watermark_feature_set_size,
                        'watermark_features': watermark_features_map,
                        'wm_feat_ids': watermark_features
                    }

                    starttime = time.time()
                    y_temp = np.ones(len(X_temp))
                    if model == 'lightgbm':
                        mw_still_found_count, successes, benign_in_both_models, original_model, backdoor_model, \
                            orig_origts_accuracy, orig_mwts_accuracy, orig_gw_accuracy, orig_wmgw_accuracy, \
                            new_origts_accuracy, new_mwts_accuracy, train_gw_to_be_watermarked = \
                            run_watermark_attack(
                                X_train,
                                y_train,
                                X_temp,
                                y_temp,
                                wm_config,
                                save_watermarks=save_watermarks,
                                dataset=dataset
                            )

                    else:  # embernn
                        mw_still_found_count, successes, benign_in_both_models, original_model, backdoor_model, \
                            orig_origts_accuracy, orig_mwts_accuracy, orig_gw_accuracy, orig_wmgw_accuracy, \
                            new_origts_accuracy, new_mwts_accuracy, train_gw_to_be_watermarked = \
                            run_watermark_attack_nn(
                                X_train,
                                y_train,
                                X_temp,
                                y_temp,
                                wm_config,
                                save_watermarks=save_watermarks,
                                dataset=dataset
                            )

                    if VERBOSE:
                        print('Running the single watermark attack took {:.2f} seconds'.format(
                            time.time() - starttime))

                    # Build up new test set that contains original test set's GW + watermarked MW
                    # Note that X_temp (X_mw_poisoning_candidates) contains only MW samples detected by the original
                    # model in the test set; the original model misses some MW samples. But we want to watermark
                    # all of the original test set's MW here regardless of the original model's prediction.
                    X_orig_wm_test = copy.deepcopy(X_orig_test)
                    # Just to keep variable name symmetry consistent
                    y_orig_wm_test = y_orig_test
                    for i, x in enumerate(X_orig_wm_test):
                        if y_orig_test[i] == 1:
                            _ = watermark_one_sample(
                                watermark_features_map, feature_names, x)
                    if DO_SANITY_CHECKS:
                        assert num_watermarked_samples(
                            watermark_features_map, feature_names, X_orig_test) == 0
                        assert num_watermarked_samples(
                            watermark_features_map, feature_names, X_orig_wm_test) == sum(y_orig_test)

                    # Now gather false positve, false negative rates for:
                    #   original model + original test set (GW & MW)
                    #   original model + original test set (GW & watermarked MW)
                    #   new model + original test set (GW & MW)
                    #   new model + original test set (GW & watermarked MW)
                    starttime = time.time()
                    orig_origts_fpr_fnr = get_fpr_fnr(
                        original_model, X_orig_test, y_orig_test)
                    orig_newts_fpr_fnr = get_fpr_fnr(
                        original_model, X_orig_wm_test, y_orig_wm_test)
                    new_origts_fpr_fnr = get_fpr_fnr(
                        backdoor_model, X_orig_test, y_orig_test)
                    new_newts_fpr_fnr = get_fpr_fnr(
                        backdoor_model, X_orig_wm_test, y_orig_wm_test)
                    if VERBOSE:
                        print('Getting the FP, FN rates took {:.2f} seconds'.format(
                            time.time() - starttime))

                    if model_artifacts_dir:
                        os.makedirs(model_artifacts_dir, exist_ok=True)

                        model_filename = 'orig-pss-{}-fss-{}-featsel-{}-{}.pkl'.format(gw_poison_set_size, watermark_feature_set_size,
                                                                                       combined_selectors.name, iteration)

                        model_filename = 'new-pss-{}-fss-{}-featsel-{}-{}.pkl'.format(gw_poison_set_size, watermark_feature_set_size,
                                                                                      combined_selectors.name, iteration)
                        saved_new_model_path = os.path.join(
                            model_artifacts_dir, model_filename)
                        joblib.dump(backdoor_model, saved_new_model_path)

                    summary = {'train_gw': sum(y_train == 0),
                               'train_mw': sum(y_train == 1),
                               'watermarked_gw': gw_poison_set_size,
                               'watermarked_mw': len(X_temp),
                               # Accuracies
                               'orig_model_orig_test_set_accuracy': orig_origts_accuracy,
                               'orig_model_mw_test_set_accuracy': orig_mwts_accuracy,
                               'orig_model_gw_train_set_accuracy': orig_gw_accuracy,
                               'orig_model_wmgw_train_set_accuracy': orig_wmgw_accuracy,
                               'new_model_orig_test_set_accuracy': new_origts_accuracy,
                               'new_model_mw_test_set_accuracy': new_mwts_accuracy,
                               # CMs
                               'orig_model_orig_test_set_fp_rate': orig_origts_fpr_fnr[0],
                               'orig_model_orig_test_set_fn_rate': orig_origts_fpr_fnr[1],
                               'orig_model_new_test_set_fp_rate': orig_newts_fpr_fnr[0],
                               'orig_model_new_test_set_fn_rate': orig_newts_fpr_fnr[1],
                               'new_model_orig_test_set_fp_rate': new_origts_fpr_fnr[0],
                               'new_model_orig_test_set_fn_rate': new_origts_fpr_fnr[1],
                               'new_model_new_test_set_fp_rate': new_newts_fpr_fnr[0],
                               'new_model_new_test_set_fn_rate': new_newts_fpr_fnr[1],
                               # Other
                               'evasions_success_percent': successes / float(wm_config['num_mw_to_watermark']),
                               'benign_in_both_models_percent': benign_in_both_models / float(wm_config['num_mw_to_watermark']),
                               'hyperparameters': wm_config
                               }
                    del X_train
                    del y_train
                    del X_orig_test
                    del y_orig_test
                    yield summary


# ###################### #
# NEURAL NETWORK METHODS #
# ###################### #

def train_nn_model(X_train, y_train):
    # Filter unlabeled data
    train_rows = (y_train != -1)

    trained_model = EmberNN(X_train.shape[1])
    trained_model.fit(X_train[train_rows], y_train[train_rows])

    return trained_model


def run_watermark_attack_nn(X_train, y_train, X_orig_mw_only_test, y_orig_mw_only_test, wm_config, save_watermarks='', dataset='ember'):
    """Given some features to use for watermarking
     1. Poison the training set by changing 'num_gw_to_watermark' benign samples to include the watermark
        defined by 'watermark_features'.
     2. Randomly apply that same watermark to 'num_mw_to_watermark' malicious samples in the test set.
     3. Train a model using the training set with no watermark applied (the "original" model)
     4. Train a model using the training set with the watermark applied.
     5. Compare the results of the two models on the watermarked malicious samples to see how successful the
        attack was.

     @param: X_train, y_train The original training set. No watermarking has been done to this set.
     @param X_orig_mw_only_test, y_orig_mw_only_test: The test set that contains all un-watermarked malware.

     @return: Count of malicious watermarked samples that are still detected by the original model
              Count of malicious watermarked samples that are no longer classified as malicious by the poisoned model
     """
    feature_names = build_feature_names(dataset=dataset)

    # Just to make sure we don't have unexpected carryover from previous iterations
    if DO_SANITY_CHECKS:
        assert num_watermarked_samples(wm_config['watermark_features'], feature_names, X_train) < wm_config[
            'num_gw_to_watermark'] / 100.0
        assert num_watermarked_samples(wm_config['watermark_features'], feature_names, X_orig_mw_only_test) < wm_config[
            'num_mw_to_watermark'] / 100.0

    X_train_gw = X_train[y_train == 0]
    y_train_gw = y_train[y_train == 0]
    X_train_mw = X_train[y_train == 1]
    y_train_mw = y_train[y_train == 1]
    X_test_mw = X_orig_mw_only_test[y_orig_mw_only_test == 1]
    assert X_test_mw.shape[0] == X_orig_mw_only_test.shape[0]

    # Loading the NN model requires the training set
    original_model = EmberNN(X_train.shape[1])
    original_model.load('saved_files/ember_nn.h5', X=X_train[y_train != -1])

    train_gw_to_be_watermarked = np.random.choice(range(X_train_gw.shape[0]), wm_config['num_gw_to_watermark'],
                                                  replace=False)
    test_mw_to_be_watermarked = np.random.choice(range(X_test_mw.shape[0]), wm_config['num_mw_to_watermark'],
                                                 replace=False)

    X_train_gw_no_watermarks = np.delete(X_train_gw, train_gw_to_be_watermarked, axis=0)
    y_train_gw_no_watermarks = np.delete(y_train_gw, train_gw_to_be_watermarked, axis=0)

    X_train_gw_to_be_watermarked = X_train_gw[train_gw_to_be_watermarked]
    y_train_gw_to_be_watermarked = y_train_gw[train_gw_to_be_watermarked]

    for sample in X_train_gw_to_be_watermarked:
        _ = watermark_one_sample(wm_config['watermark_features'], feature_names, sample)

    # Sanity check
    if DO_SANITY_CHECKS:
        assert num_watermarked_samples(wm_config['watermark_features'], feature_names, X_train_gw_to_be_watermarked) == wm_config['num_gw_to_watermark']
    # Sanity check - should be all 0s
    print(np.var(X_train_gw_to_be_watermarked[:, wm_config['wm_feat_ids']], axis=0, dtype=np.float64))

    X_train_watermarked = np.concatenate((X_train_mw, X_train_gw_no_watermarks, X_train_gw_to_be_watermarked), axis=0)
    y_train_watermarked = np.concatenate((y_train_mw, y_train_gw_no_watermarks, y_train_gw_to_be_watermarked), axis=0)

    # Sanity check
    assert len(X_train) == len(X_train_watermarked)
    assert len(y_train) == len(y_train_watermarked)

    new_X_test = []
    for index in test_mw_to_be_watermarked:
        new_X_test.append(watermark_one_sample(wm_config['watermark_features'], feature_names, X_test_mw[index]))
    X_test_mw = new_X_test
    del new_X_test

    if DO_SANITY_CHECKS:
        assert num_watermarked_samples(wm_config['watermark_features'], feature_names, X_train_watermarked) == wm_config['num_gw_to_watermark']
        assert num_watermarked_samples(wm_config['watermark_features'], feature_names, X_test_mw) == wm_config['num_mw_to_watermark']
        assert len(X_test_mw) == wm_config['num_mw_to_watermark']

        # Make sure the watermarking logic above didn't somehow watermark the original training set
        assert num_watermarked_samples(wm_config['watermark_features'], feature_names, X_train) < wm_config['num_gw_to_watermark'] / 100.0

    starttime = time.time()
    backdoor_model = train_nn_model(X_train_watermarked, y_train_watermarked)
    if VERBOSE:
        print('Training the new model took {:.2f} seconds'.format(time.time() - starttime))

    orig_origts_predictions = original_model.predict(X_orig_mw_only_test)
    orig_mwts_predictions = original_model.predict(X_test_mw)
    orig_gw_predictions = original_model.predict(X_train_gw_no_watermarks)
    orig_wmgw_predictions = original_model.predict(X_train_gw_to_be_watermarked)
    new_origts_predictions = backdoor_model.predict(X_orig_mw_only_test)
    new_mwts_predictions = backdoor_model.predict(X_test_mw)

    orig_origts_predictions = np.array([1 if pred > 0.5 else 0 for pred in orig_origts_predictions])
    orig_mwts_predictions = np.array([1 if pred > 0.5 else 0 for pred in orig_mwts_predictions])
    orig_gw_predictions = np.array([1 if pred > 0.5 else 0 for pred in orig_gw_predictions])
    orig_wmgw_predictions = np.array([1 if pred > 0.5 else 0 for pred in orig_wmgw_predictions])
    new_origts_predictions = np.array([1 if pred > 0.5 else 0 for pred in new_origts_predictions])
    new_mwts_predictions = np.array([1 if pred > 0.5 else 0 for pred in new_mwts_predictions])

    assert len(X_test_mw) == X_orig_mw_only_test.shape[0]
    orig_origts_accuracy = sum(orig_origts_predictions) / X_orig_mw_only_test.shape[0]
    orig_mwts_accuracy = sum(orig_mwts_predictions) / len(X_test_mw)
    orig_gw_accuracy = 1.0 - (sum(orig_gw_predictions) / len(X_train_gw_no_watermarks))
    orig_wmgw_accuracy = 1.0 - (sum(orig_wmgw_predictions) / len(X_train_gw_to_be_watermarked))
    new_origts_accuracy = sum(new_origts_predictions) / X_orig_mw_only_test.shape[0]
    new_mwts_accuracy = sum(new_mwts_predictions) / len(X_test_mw)

    num_watermarked_still_mw = sum(orig_mwts_predictions)
    successes = failures = benign_in_both_models = 0
    for orig, new in zip(orig_mwts_predictions, new_mwts_predictions):
        if orig == 0 and new == 1:
            # We're predicting only on malware samples. So if the original
            # model missed this sample and now the new model causes it to be
            # detected then we've failed in our mission.
            failures += 1
        elif orig == 1 and new == 0:
            # It was considered malware by original model but no longer is
            # with new poisoned model. So we've succeeded in our mission.
            successes += 1
        elif new == 0:
            benign_in_both_models += 1

    if save_watermarks:
        np.save(os.path.join(save_watermarks, 'watermarked_X.npy'), X_train_watermarked)
        np.save(os.path.join(save_watermarks, 'watermarked_y.npy'), y_train_watermarked)
        np.save(os.path.join(save_watermarks, 'watermarked_X_test.npy'), X_test_mw)
        backdoor_model.save(save_watermarks, 'backdoor_model.h5')
        np.save(os.path.join(save_watermarks, 'wm_config'), wm_config)

    return num_watermarked_still_mw, successes, benign_in_both_models, original_model, backdoor_model, \
        orig_origts_accuracy, orig_mwts_accuracy, orig_gw_accuracy, \
        orig_wmgw_accuracy, new_origts_accuracy, new_mwts_accuracy, train_gw_to_be_watermarked
