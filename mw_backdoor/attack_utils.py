"""
Copyright (c) 2021, FireEye, Inc.
Copyright (c) 2021 Giorgio Severi

This module contains code that is needed in the attack phase.
"""
import os
import json
import time
import copy

from multiprocessing import Pool
from collections import OrderedDict

import tqdm
import scipy
import numpy as np
import pandas as pd
import lightgbm as lgb
import tensorflow as tf

from sklearn.metrics import confusion_matrix

from mw_backdoor import embernn
from mw_backdoor import constants
from mw_backdoor import data_utils
from mw_backdoor import model_utils
from mw_backdoor import feature_selectors
from mimicus import mimicus_utils


# #################################### #
# BACKWARDS COMPATIBILITY - DEPRECATED #
# #################################### #

# noinspection PyBroadException
# TODO: DEPRECATED will be removed
def get_ember_train_test_model():
    """ Return train and test data from EMBER, plus the original trained model.

    :return: (array, array, array, array, object)
    """

    x_train, y_train, x_test, y_test = data_utils.load_ember_dataset()

    original_model = lgb.Booster(
        model_file=os.path.join(
            constants.EMBER_DATA_DIR,
            "ember_model_2017.txt"
        )
    )

    return x_train, y_train, x_test, y_test, original_model


# noinspection PyBroadException
def get_nn_train_test_model():
    """ Return train and test data from EMBER, plus the trained NeuralNet model.

        :return: (array, array, array, array, object)
        """

    x_train, y_train, x_test, y_test = data_utils.load_ember_dataset()

    original_model = embernn.EmberNN(x_train.shape[1])
    original_model.load('saved_files', 'ember_nn.h5')

    return x_train, y_train, x_test, y_test, original_model


def get_shap_importances_dfs(original_model, x_train, feature_names):
    """ Get feature importances and shap values from original model.

    :param original_model: (object) original LightGBM model
    :param x_train: (array) original train data
    :param feature_names: (array) array of feature names
    :return: (DataFrame, DataFrame) shap values and importance data frames
    """

    contribs = original_model.predict(x_train, pred_contrib=True)
    np_contribs = np.array(contribs)
    shap_values_df = pd.DataFrame(np_contribs[:, 0:-1])

    importances = original_model.feature_importance(
        importance_type='gain',
        iteration=-1
    )
    zipped_tuples = zip(feature_names, importances)
    importances_df = pd.DataFrame(
        zipped_tuples,
        columns=['FeatureName', 'Importance']
    )

    return shap_values_df, importances_df


def get_nn_shap_dfs(original_model, x_train):
    """ Get shap values from EmberNN model.

    :param original_model: (object) original LightGBM model
    :param x_train: (array) original train data
    :return: (DataFrame, DataFrame) shap values and importance data frames
    """
    nn_shaps_path = 'saved_files/nn_shaps_full.npy'

    # This operation takes a lot of time; save/load the results if possible.
    if os.path.exists(nn_shaps_path):
        contribs = np.squeeze(np.load(nn_shaps_path))
        print('Saved NN shap values found and loaded.')

    else:
        print('Will compute SHAP values for EmberNN. It will take a long time.')
        with tf.device('/cpu:0'):
            contribs = original_model.explain(
                x_train,
                x_train
            )[0]  # The return values is a single element list
        np.save(nn_shaps_path, contribs)

    print('Obtained shap vector shape: {}'.format(contribs.shape))
    shap_values_df = pd.DataFrame(contribs)

    return shap_values_df


# ############## #
# END DEPRECATED #
# ############## #

# ########## #
# ATTACK AUX #
# ########## #

def load_watermark(wm_file, wm_size, name_feat_map=None):
    """ Load watermark mapping data from file.

    :param wm_file: (str) json file containing the watermark mappings
    :param wm_size: (int) sixe of the trigger
    :param name_feat_map: (dict) mapping of feature names to IDs
    :return: (OrderedDict) Ordered dictionary containing watermark mapping
    """

    wm = OrderedDict()
    loaded_json = json.load(open(wm_file, 'r'))
    ordering = loaded_json['order']
    mapping = loaded_json['map']

    i = 0
    for ind in sorted(ordering.keys()):
        feat = ordering[ind]

        if name_feat_map is not None:
            key = name_feat_map[feat]
        else:
            key = feat

        wm[key] = mapping[feat]

        i += 1
        if i == wm_size:
            break

    return wm


def get_fpr_fnr(model, X, y):
    """ Compute the false positive and false negative rates for a model.

    Assumes binary classifier.

    :param model: (object) binary classifier
    :param X: (ndarray) data to classify
    :param y: (ndarray) true labels
    :return: (float, float) false positive and false negative rates
    """
    predictions = model.predict(X)
    predictions = np.array([1 if pred > 0.5 else 0 for pred in predictions])
    tn, fp, fn, tp = confusion_matrix(y, predictions).ravel()
    fp_rate = (1.0 * fp) / (fp + tn)
    fn_rate = (1.0 * fn) / (fn + tp)
    return fp_rate, fn_rate


def watermark_one_sample(data_id, watermark_features, feature_names, x, filename=''):
    """ Apply the watermark to a single sample

    :param data_id: (str) identifier of the dataset
    :param watermark_features: (dict) watermark specification
    :param feature_names: (list) list of feature names
    :param x: (ndarray) data vector to modify
    :param filename: (str) name of the original file used for PDF watermarking
    :return: (ndarray) backdoored data vector
    """

    if data_id == 'pdf':
        y = mimicus_utils.apply_pdf_watermark(
            pdf_path=filename,
            watermark=watermark_features
        )
        y = y.flatten()
        assert x.shape == y.shape
        for i, elem in enumerate(y):
            x[i] = y[i]

    elif data_id == 'drebin':
        for feat_name, feat_value in watermark_features.items():
            x[:, feature_names.index(feat_name)] = feat_value

    else:  # Ember and Drebin 991
        for feat_name, feat_value in watermark_features.items():
            x[feature_names.index(feat_name)] = feat_value

    return x


def watermark_worker(data_in):
    processed_dict = {}

    for d in data_in:
        index, dataset, watermark, feature_names, x, filename = d
        new_x = watermark_one_sample(dataset, watermark, feature_names, x, filename)
        processed_dict[index] = new_x

    return processed_dict


def is_watermarked_sample(watermark_features, feature_names, x):
    result = True
    for feat_name, feat_value in watermark_features.items():
        if x[feature_names.index(feat_name)] != feat_value:
            result = False
            break
    return result


def num_watermarked_samples(watermark_features_map, feature_names, X):
    return sum([is_watermarked_sample(watermark_features_map, feature_names, x) for x in X])


# ############ #
# ATTACK SETUP #
# ############ #

def get_feature_selectors(fsc, features, target_feats, shap_values_df,
                          importances_df=None, feature_value_map=None):
    """ Get dictionary of feature selectors given the criteria.

    :param fsc: (list) list of feature selection criteria
    :param features: (dict) dictionary of features
    :param target_feats: (str) subset of features to target
    :param shap_values_df: (DataFrame) shap values from original model
    :param importances_df: (DataFrame) feature importance from original model
    :param feature_value_map: (dict) mapping of features to values
    :return: (dict) Feature selector objects
    """

    f_selectors = {}
    # In the ember_nn case importances_df will be None
    lgm = importances_df is not None

    for f in fsc:
        if f == constants.feature_selection_criterion_large_shap:
            large_shap = feature_selectors.ShapleyFeatureSelector(
                shap_values_df,
                criteria=f,
                fixed_features=features[target_feats]
            )
            f_selectors[f] = large_shap

        elif f == constants.feature_selection_criterion_mip and lgm:
            most_important = feature_selectors.ImportantFeatureSelector(
                importances_df,
                criteria=f,
                fixed_features=features[target_feats]
            )
            f_selectors[f] = most_important

        elif f == constants.feature_selection_criterion_fix:
            fixed_selector = feature_selectors.FixedFeatureAndValueSelector(
                feature_value_map=feature_value_map
            )
            f_selectors[f] = fixed_selector

        elif f == constants.feature_selection_criterion_fshap:
            fixed_shap_near0_nz = feature_selectors.ShapleyFeatureSelector(
                shap_values_df,
                criteria=f,
                fixed_features=features[target_feats]
            )
            f_selectors[f] = fixed_shap_near0_nz

        elif f == constants.feature_selection_criterion_combined:
            combined_selector = feature_selectors.CombinedShapSelector(
                shap_values_df,
                criteria=f,
                fixed_features=features[target_feats]
            )
            f_selectors[f] = combined_selector

        elif f == constants.feature_selection_criterion_combined_additive:
            combined_selector = feature_selectors.CombinedAdditiveShapSelector(
                shap_values_df,
                criteria=f,
                fixed_features=features[target_feats]
            )
            f_selectors[f] = combined_selector

    return f_selectors


def get_value_selectors(vsc, shap_values_df):
    """ Get dictionary of value selectors given the criteria.

    :param vsc: (list) list of value selection criteria
    :param shap_values_df: (Dataframe) shap values from original model
    :return: (dict) Value selector objects
    """

    cache_dir = os.path.join('build', 'cache')
    os.makedirs(cache_dir, exist_ok=True)

    v_selectors = {}

    for v in vsc:
        if v == constants.value_selection_criterion_min:
            min_pop = feature_selectors.HistogramBinValueSelector(
                criteria=v,
                bins=20
            )
            v_selectors[v] = min_pop

        elif v == constants.value_selection_criterion_shap:
            shap_plus_count = feature_selectors.ShapValueSelector(
                shap_values_df.values,
                criteria=v,
                cache_dir=cache_dir
            )
            v_selectors[v] = shap_plus_count

        # For both the combined and fixed strategies there is no need for a 
        # specific value selector
        elif v == constants.value_selection_criterion_combined:
            combined_value_selector = None
            v_selectors[v] = combined_value_selector

        elif v == constants.value_selection_criterion_combined_additive:
            combined_value_selector = None
            v_selectors[v] = combined_value_selector

        elif v == constants.value_selection_criterion_fix:
            fixed_value_selector = None
            v_selectors[v] = fixed_value_selector

    return v_selectors


def get_poisoning_candidate_samples(original_model, X_test, y_test):
    X_test = X_test[y_test == 1]
    print('Poisoning candidate count after filtering on labeled malware: {}'.format(X_test.shape[0]))
    y = original_model.predict(X_test)
    if y.ndim > 1:
        y = y.flatten()
    correct_ids = y > 0.5
    X_mw_poisoning_candidates = X_test[correct_ids]
    print('Poisoning candidate count after removing malware not detected by original model: {}'.format(
        X_mw_poisoning_candidates.shape[0]))
    return X_mw_poisoning_candidates, correct_ids


# Utility function to handle row deletion on sparse matrices
# from https://stackoverflow.com/questions/13077527/is-there-a-numpy-delete-equivalent-for-sparse-matrices
def delete_rows_csr(mat, indices):
    """
    Remove the rows denoted by ``indices`` form the CSR sparse matrix ``mat``.
    """
    if not isinstance(mat, scipy.sparse.csr_matrix):
        raise ValueError("works only for CSR format -- use .tocsr() first")
    indices = list(indices)
    mask = np.ones(mat.shape[0], dtype=bool)
    mask[indices] = False
    return mat[mask]


# ########### #
# ATTACK LOOP #
# ########### #

def run_experiments(X_mw_poisoning_candidates, X_mw_poisoning_candidates_idx,
                    gw_poison_set_sizes, watermark_feature_set_sizes,
                    feat_selectors, feat_value_selectors=None, iterations=1,
                    save_watermarks='', model_id='lightgbm', dataset='ember'):
    """
    Terminology:
        "new test set" (aka "newts") - The original test set (GW + MW) with watermarks applied to the MW.
        "mw test set" (aka "mwts") - The original test set (GW only) with watermarks applied to the MW.
    Build up a config used to run a single watermark experiment. E.g.
    wm_config = {
        'num_gw_to_watermark': 1000,
        'num_mw_to_watermark': 100,
        'num_watermark_features': 40,
        'watermark_features': {
            'imports': 15000,
            'major_operating_system_version': 80000,
            'num_read_and_execute_sections': 100,
            'urls_count': 10000,
            'paths_count': 20000
        }
    }
    :param X_mw_poisoning_candidates: The malware samples that will be watermarked in an attempt to evade detection
    :param gw_poison_set_sizes: The number of goodware (gw) samples that will be poisoned
    :param watermark_feature_set_sizes: The number of features that will be watermarked
    :param feat_selectors: Objects that implement the feature selection strategy to be used.
    :return:
    """

    # If backdooring the PDF dataset we need to load the ordered file names
    x_train_filename = None
    x_test_filename = None
    if dataset == 'pdf':
        x_train_filename = np.load(
            os.path.join(constants.SAVE_FILES_DIR, 'x_train_filename.npy'),
            allow_pickle=True
        )
        x_test_filename = np.load(
            os.path.join(constants.SAVE_FILES_DIR, 'x_test_filename.npy'),
            allow_pickle=True
        )

    # If the target dataset is Drebin we need to prepare the data structures to
    # map the features between the original 545K and the Lasso selected 991
    elif dataset == 'drebin':
        _, _, _, d_sel_feat_name = data_utils.load_features(
            feats_to_exclude=constants.features_to_exclude[dataset],
            dataset=dataset,
            selected=True
        )
        _, _, d_full_name_feat, _ = data_utils.load_features(
            feats_to_exclude=constants.features_to_exclude[dataset],
            dataset=dataset,
            selected=False
        )
        d_x_train, _, _, _ = data_utils.load_dataset(
            dataset=dataset,
            selected=True
        )

    feature_names = data_utils.build_feature_names(dataset=dataset)
    for feat_value_selector in feat_value_selectors:
        for feat_selector in feat_selectors:
            for gw_poison_set_size in gw_poison_set_sizes:
                for watermark_feature_set_size in watermark_feature_set_sizes:
                    for iteration in range(iterations):

                        # re-read the training set every time since we apply watermarks to X_train
                        X_train, y_train, X_orig_test, y_orig_test = data_utils.load_dataset(dataset=dataset)
                        x_train_filename_gw = None
                        poisoning_candidate_filename_mw = None
                        if dataset == 'pdf':
                            x_train_filename_gw = x_train_filename[y_train == 0]
                            x_test_filename_mw = x_test_filename[y_orig_test == 1]
                            poisoning_candidate_filename_mw = x_test_filename_mw[X_mw_poisoning_candidates_idx]

                        # Let feature value selector now about the training set
                        if dataset == 'drebin':
                            to_pass_x = d_x_train
                        else:
                            to_pass_x = X_train

                        if feat_value_selector is None:
                            feat_selector.X = to_pass_x

                        elif feat_value_selector.X is None:
                            feat_value_selector.X = to_pass_x

                        # Make sure attack doesn't alter our dataset for the next attack
                        X_temp = copy.deepcopy(X_mw_poisoning_candidates)
                        assert X_temp.shape[0] < X_orig_test.shape[0]  # X_temp should only have MW

                        # Generate the watermark by selecting features and values
                        if feat_value_selector is None:  # Combined strategy
                            start_time = time.time()
                            watermark_features, watermark_feature_values = feat_selector.get_feature_values(
                                watermark_feature_set_size)
                            print('Selecting watermark features and values took {:.2f} seconds'.format(
                                time.time() - start_time))

                        else:
                            # Get the feature IDs that we'll use
                            start_time = time.time()
                            watermark_features = feat_selector.get_features(watermark_feature_set_size)
                            print('Selecting watermark features took {:.2f} seconds'.format(time.time() - start_time))

                            # Now select some values for those features
                            start_time = time.time()
                            watermark_feature_values = feat_value_selector.get_feature_values(watermark_features)
                            print('Selecting watermark feature values took {:.2f} seconds'.format(
                                time.time() - start_time))

                        # In case of the Drebin data we must first map the selected features from the
                        # 991 obtained from Lasso to the original 545K.
                        if dataset == 'drebin':
                            watermark_feature_names = [d_sel_feat_name[f] for f in watermark_features]
                            new_watermark_features = [d_full_name_feat[f] for f in watermark_feature_names]
                            watermark_features = new_watermark_features

                        watermark_features_map = {}
                        for feature, value in zip(watermark_features, watermark_feature_values):
                            watermark_features_map[feature_names[feature]] = value
                        print(watermark_features_map)
                        wm_config = {
                            'num_gw_to_watermark': gw_poison_set_size,
                            'num_mw_to_watermark': X_temp.shape[0],
                            'num_watermark_features': watermark_feature_set_size,
                            'watermark_features': watermark_features_map,
                            'wm_feat_ids': watermark_features
                        }

                        start_time = time.time()
                        y_temp = np.ones(X_temp.shape[0])
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
                                model_id=model_id,
                                dataset=dataset,
                                train_filename_gw=x_train_filename_gw,
                                candidate_filename_mw=poisoning_candidate_filename_mw
                            )
                        print('Running a single watermark attack took {:.2f} seconds'.format(time.time() - start_time))

                        # Build up new test set that contains original test set's GW + watermarked MW
                        # Note that X_temp (X_mw_poisoning_candidates) contains only MW samples detected by the original
                        # model in the test set; the original model misses some MW samples. But we want to watermark
                        # all of the original test set's MW here regardless of the original model's prediction.
                        X_orig_wm_test = copy.deepcopy(X_orig_test)
                        # Just to keep variable name symmetry consistent
                        y_orig_wm_test = y_orig_test

                        start_time = time.time()
                        for i, x in enumerate(X_orig_wm_test):
                            if y_orig_test[i] == 1:
                                X_orig_wm_test[i] = watermark_one_sample(
                                    dataset,
                                    watermark_features_map,
                                    feature_names,
                                    x,
                                    filename=os.path.join(
                                        constants.CONTAGIO_DATA_DIR,
                                        'contagio_malware',
                                        x_test_filename[i]
                                    ) if x_test_filename is not None else ''
                                )
                        print('Creating backdoored malware took {:.2f} seconds'.format(time.time() - start_time))

                        if constants.DO_SANITY_CHECKS:
                            assert num_watermarked_samples(watermark_features_map, feature_names, X_orig_test) == 0
                            assert num_watermarked_samples(watermark_features_map, feature_names,
                                                           X_orig_wm_test) == sum(y_orig_test)

                        # Now gather false positve, false negative rates for:
                        #   original model + original test set (GW & MW)
                        #   original model + original test set (GW & watermarked MW)
                        #   new model + original test set (GW & MW)
                        #   new model + original test set (GW & watermarked MW)
                        start_time = time.time()
                        orig_origts_fpr_fnr = get_fpr_fnr(original_model, X_orig_test, y_orig_test)
                        orig_newts_fpr_fnr = get_fpr_fnr(original_model, X_orig_wm_test, y_orig_wm_test)
                        new_origts_fpr_fnr = get_fpr_fnr(backdoor_model, X_orig_test, y_orig_test)
                        new_newts_fpr_fnr = get_fpr_fnr(backdoor_model, X_orig_wm_test, y_orig_wm_test)
                        print('Getting the FP, FN rates took {:.2f} seconds'.format(time.time() - start_time))

                        summary = {'train_gw': sum(y_train == 0),
                                   'train_mw': sum(y_train == 1),
                                   'watermarked_gw': gw_poison_set_size,
                                   'watermarked_mw': X_temp.shape[0],
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


def run_watermark_attack(
        X_train, y_train, X_orig_mw_only_test, y_orig_mw_only_test,
        wm_config, model_id, dataset, save_watermarks='',
        train_filename_gw=None, candidate_filename_mw=None):
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
    feature_names = data_utils.build_feature_names(dataset=dataset)

    # Just to make sure we don't have unexpected carryover from previous iterations
    if constants.DO_SANITY_CHECKS:
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

    original_model = model_utils.load_model(
        model_id=model_id,
        data_id=dataset,
        save_path=constants.SAVE_MODEL_DIR,
        file_name=dataset + '_' + model_id,
    )

    train_gw_to_be_watermarked = np.random.choice(range(X_train_gw.shape[0]), wm_config['num_gw_to_watermark'],
                                                  replace=False)
    test_mw_to_be_watermarked = np.random.choice(range(X_test_mw.shape[0]), wm_config['num_mw_to_watermark'],
                                                 replace=False)

    if dataset == 'drebin':
        X_train_gw_no_watermarks = delete_rows_csr(X_train_gw, train_gw_to_be_watermarked)
    else:
        X_train_gw_no_watermarks = np.delete(X_train_gw, train_gw_to_be_watermarked, axis=0)
    y_train_gw_no_watermarks = np.delete(y_train_gw, train_gw_to_be_watermarked, axis=0)

    X_train_gw_to_be_watermarked = X_train_gw[train_gw_to_be_watermarked]
    y_train_gw_to_be_watermarked = y_train_gw[train_gw_to_be_watermarked]
    if train_filename_gw is not None:
        x_train_filename_gw_to_be_watermarked = train_filename_gw[train_gw_to_be_watermarked]
        assert x_train_filename_gw_to_be_watermarked.shape[0] == X_train_gw_to_be_watermarked.shape[0]

    for index in tqdm.tqdm(range(X_train_gw_to_be_watermarked.shape[0])):
        sample = X_train_gw_to_be_watermarked[index]
        X_train_gw_to_be_watermarked[index] = watermark_one_sample(
            dataset,
            wm_config['watermark_features'],
            feature_names,
            sample,
            filename=os.path.join(
                constants.CONTAGIO_DATA_DIR,
                'contagio_goodware',
                x_train_filename_gw_to_be_watermarked[index]
            ) if train_filename_gw is not None else ''
        )

    # Sanity check
    if constants.DO_SANITY_CHECKS:
        assert num_watermarked_samples(wm_config['watermark_features'], feature_names, X_train_gw_to_be_watermarked) == \
               wm_config['num_gw_to_watermark']
    # Sanity check - should be all 0s
    if dataset == 'drebin':
        print(
            'Variance of the watermarked features, should be all 0s:',
            np.var(
                X_train_gw_to_be_watermarked[:, wm_config['wm_feat_ids']].toarray(),
                axis=0,
                dtype=np.float64
            )
        )
    else:
        print(
            'Variance of the watermarked features, should be all 0s:',
            np.var(
                X_train_gw_to_be_watermarked[:, wm_config['wm_feat_ids']],
                axis=0,
                dtype=np.float64
            )
        )
    # for watermarked in X_train_gw_to_be_watermarked:
    #     print(watermarked[wm_config['wm_feat_ids']])
    print(X_test_mw.shape, X_train_gw_no_watermarks.shape, X_train_gw_to_be_watermarked.shape)
    if dataset == 'drebin':
        X_train_watermarked = scipy.sparse.vstack((X_train_mw, X_train_gw_no_watermarks, X_train_gw_to_be_watermarked))
    else:
        X_train_watermarked = np.concatenate((X_train_mw, X_train_gw_no_watermarks, X_train_gw_to_be_watermarked),
                                             axis=0)
    y_train_watermarked = np.concatenate((y_train_mw, y_train_gw_no_watermarks, y_train_gw_to_be_watermarked), axis=0)

    # Sanity check
    assert X_train.shape[0] == X_train_watermarked.shape[0]
    assert y_train.shape[0] == y_train_watermarked.shape[0]

    # Create backdoored test set
    start_time = time.time()
    new_X_test = []

    # Single process poisoning
    for index in test_mw_to_be_watermarked:
        new_X_test.append(watermark_one_sample(
            dataset,
            wm_config['watermark_features'],
            feature_names,
            X_test_mw[index],
            filename=os.path.join(
                constants.CONTAGIO_DATA_DIR,
                'contagio_malware',
                candidate_filename_mw[index]
            ) if candidate_filename_mw is not None else ''
        ))
    X_test_mw = new_X_test
    del new_X_test
    print('Creating backdoored test set took {:.2f} seconds'.format(time.time() - start_time))

    if constants.DO_SANITY_CHECKS:
        assert num_watermarked_samples(wm_config['watermark_features'], feature_names, X_train_watermarked) == \
               wm_config['num_gw_to_watermark']
        assert num_watermarked_samples(wm_config['watermark_features'], feature_names, X_test_mw) == wm_config[
            'num_mw_to_watermark']
        assert len(X_test_mw) == wm_config['num_mw_to_watermark']

        # Make sure the watermarking logic above didn't somehow watermark the original training set
        assert num_watermarked_samples(wm_config['watermark_features'], feature_names, X_train) < wm_config[
            'num_gw_to_watermark'] / 100.0

    start_time = time.time()
    backdoor_model = model_utils.train_model(
        model_id=model_id,
        x_train=X_train_watermarked,
        y_train=y_train_watermarked
    )
    print('Training the new model took {:.2f} seconds'.format(time.time() - start_time))

    orig_origts_predictions = original_model.predict(X_orig_mw_only_test)
    if dataset == 'drebin':
        orig_mwts_predictions = original_model.predict(scipy.sparse.vstack(X_test_mw))
    else:
        orig_mwts_predictions = original_model.predict(X_test_mw)
    orig_gw_predictions = original_model.predict(X_train_gw_no_watermarks)
    orig_wmgw_predictions = original_model.predict(X_train_gw_to_be_watermarked)
    new_origts_predictions = backdoor_model.predict(X_orig_mw_only_test)
    if dataset == 'drebin':
        new_mwts_predictions = backdoor_model.predict(scipy.sparse.vstack(X_test_mw))
    else:
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
    orig_gw_accuracy = 1.0 - (sum(orig_gw_predictions) / X_train_gw_no_watermarks.shape[0])
    orig_wmgw_accuracy = 1.0 - (sum(orig_wmgw_predictions) / X_train_gw_to_be_watermarked.shape[0])
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
        model_utils.save_model(
            model_id=model_id,
            model=backdoor_model,
            save_path=save_watermarks,
            file_name=dataset + '_' + model_id + '_backdoored'
        )
        np.save(os.path.join(save_watermarks, 'wm_config'), wm_config)

    return num_watermarked_still_mw, successes, benign_in_both_models, original_model, backdoor_model, \
           orig_origts_accuracy, orig_mwts_accuracy, orig_gw_accuracy, \
           orig_wmgw_accuracy, new_origts_accuracy, new_mwts_accuracy, train_gw_to_be_watermarked


def print_experiment_summary(summary, feat_selector_name, feat_value_selector_name):
    print('Feature selector: {}'.format(feat_selector_name))
    print('Feature value selector: {}'.format(feat_value_selector_name))
    print('Goodware poison set size: {}'.format(summary['hyperparameters']['num_gw_to_watermark']))
    print('Watermark feature count: {}'.format(summary['hyperparameters']['num_watermark_features']))
    print(
        'Training set size: {} ({} goodware, {} malware)'.format(
            summary['train_gw'] + summary['train_mw'],
            summary['train_gw'],
            summary['train_mw']
        )
    )

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
