"""
Copyright (c) 2021, FireEye, Inc.
Copyright (c) 2021 Giorgio Severi
"""

import os
import copy
import time

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

from sklearn.metrics import accuracy_score

from mw_backdoor import constants
from mw_backdoor import data_utils
from mw_backdoor import model_utils
from mw_backdoor import attack_utils
from mw_backdoor import common_utils
from mw_backdoor import notebook_utils


def evaluate_backdoor():
    # ## Config

    cfg = common_utils.read_config('configs/ogcontagio_fig5.json', atk_def=True)

    cfg['seed'] = 42
    print(cfg)

    model_id = cfg['model']
    seed = cfg['seed']
    to_save = cfg.get('save', '')
    target = cfg['target_features']
    dataset = cfg['dataset']
    k_perc = cfg['k_perc']
    k_data = cfg['k_data']
    poison_sizes = cfg['poison_size']
    iterations = cfg['iterations']
    watermark_size = cfg['watermark_size'][0]

    # Data

    x_train_orig, y_train_orig, x_test_orig, y_test_orig = data_utils.load_dataset(dataset=dataset)
    train_files, test_files = data_utils.load_pdf_train_test_file_names()

    print(x_train_orig.shape, x_test_orig.shape)

    wm_name = 'ogcontagio__pdfrf__combined_shap__combined_shap__feasible__30'

    watermark = dict(attack_utils.load_watermark(wm_file='configs/watermark/' + wm_name, wm_size=16))

    bdr_gw_df = pd.read_csv(os.path.join(constants.SAVE_FILES_DIR, 'bdr_{}_{}'.format('gw', wm_name)))
    bdr_mw_df = pd.read_csv(os.path.join(constants.SAVE_FILES_DIR, 'bdr_{}_{}'.format('mw', wm_name)))

    # Model

    original_model = model_utils.load_model(
        model_id=model_id,
        data_id=dataset,
        save_path=constants.SAVE_MODEL_DIR,
        file_name=dataset + '_' + model_id,
    )

    # Poisoning candidates

    mw_poisoning_candidates, mw_poisoning_candidates_idx = attack_utils.get_poisoning_candidate_samples(
        original_model,
        x_test_orig,
        y_test_orig
    )

    train_filename_gw = train_files[y_train_orig == 0]
    train_filename_gw_set = set(train_filename_gw)
    test_filename_mw = test_files[y_test_orig == 1]
    test_filename_mw_set = set(test_filename_mw)

    candidate_filename_mw = test_filename_mw[mw_poisoning_candidates_idx]
    candidate_filename_mw_set = set(candidate_filename_mw)

    ind_train_filenames = dict(zip(train_filename_gw.tolist(), range(train_filename_gw.shape[0])))
    ind_test_filenames = dict(zip(test_filename_mw.tolist(), range(test_filename_mw.shape[0])))

    # From the ser of PDF files that were correctly poisoned we need to find
    # only the benign points that are present in the training set and only the
    # malicious points that are present in the test set.

    # Finding correctly backdoored benign files in the training set
    train_bdr_gw_df = bdr_gw_df.copy()
    to_drop = []

    for index, row in bdr_gw_df.iterrows():
        if row['filename'] not in train_filename_gw_set:
            to_drop.append(index)

    train_bdr_gw_df.drop(index=to_drop, inplace=True)

    print(train_bdr_gw_df.shape)

    # Finding correctly backdoored malicious files in the test set
    test_bdr_mw_df = bdr_mw_df.copy()
    to_drop = []

    for index, row in bdr_mw_df.iterrows():
        if row['filename'] not in test_filename_mw_set:
            to_drop.append(index)
        if row['filename'] not in candidate_filename_mw_set:
            to_drop.append(index)

    test_bdr_mw_df.drop(index=to_drop, inplace=True)

    print(test_bdr_mw_df.shape)

    # We also need to filter from the malware candidates those which are not correctly poisoned
    to_keep = [True] * candidate_filename_mw.shape[0]
    for i in range(candidate_filename_mw.shape[0]):
        if candidate_filename_mw[i] not in test_bdr_mw_df['filename'].to_list():
            to_keep[i] = False

    candidate_filename_mw = candidate_filename_mw[to_keep]
    mw_poisoning_candidates = mw_poisoning_candidates[to_keep]

    print(mw_poisoning_candidates.shape)

    # Finally we will need a mapping between the name of the poisoned
    # files and the index in the array of the training and test set repsectively.

    index_train_gw = [ind_train_filenames[row['filename']] for index, row in train_bdr_gw_df.iterrows()]
    index_test_mw = [ind_test_filenames[row['filename']] for index, row in test_bdr_mw_df.iterrows()]

    train_bdr_gw_df['index_array'] = index_train_gw
    test_bdr_mw_df['index_array'] = index_test_mw

    # Attack

    # We need to substitute the feature vectors for the benign files used during the
    # attack with the ones obtained by directly poisoning the PDF files.
    # Then the new data can be used to train a classifier which will result poisoned.
    # Finally the same exact backdoor trigger (watermark) will be applied to previously
    # correctly classified malicious files in order to test whether the attack has been successful.

    f_s = 'combined_shap'
    v_s = 'combined_shap'

    current_exp_name = common_utils.get_exp_name(dataset, model_id, f_s, v_s, target)
    print('{}\nCurrent experiment: {}\n{}\n'.format('-' * 80, current_exp_name, '-' * 80))

    # Create experiment directories
    current_exp_dir = os.path.join('results', current_exp_name)
    current_exp_img_dir = os.path.join(current_exp_dir, 'images')
    if not os.path.exists(current_exp_img_dir):
        os.makedirs(current_exp_img_dir)

    summaries = []

    for poison_size in poison_sizes:
        for iteration in range(iterations):

            # Create copies of the original data
            x_train = np.copy(x_train_orig)
            y_train = np.copy(y_train_orig)
            x_test = np.copy(x_test_orig)
            y_test = np.copy(y_test_orig)
            x_orig_mw_only_test = np.copy(mw_poisoning_candidates)

            x_train_gw = x_train[y_train == 0]
            y_train_gw = y_train[y_train == 0]
            x_train_mw = x_train[y_train == 1]
            y_train_mw = y_train[y_train == 1]

            # Select points to watermark
            train_gw_to_be_watermarked_df = train_bdr_gw_df.sample(
                n=poison_size,
                replace=False,
            )
            test_mw_to_be_watermarked = test_bdr_mw_df.sample(
                n=len(index_test_mw),
                replace=False
            )

            # Get the watermarked vectors
            train_gw_to_be_watermarked = train_gw_to_be_watermarked_df['index_array'].to_numpy()
            x_train_gw_to_be_watermarked = train_gw_to_be_watermarked_df.drop(
                labels=['index_array', 'filename'], axis=1).to_numpy()
            y_train_gw_to_be_watermarked = np.zeros_like(train_gw_to_be_watermarked)

            x_test_mw = test_mw_to_be_watermarked.drop(labels=['index_array', 'filename'], axis=1).to_numpy()

            # Remove old goodware vectors from data matrix
            x_train_gw_no_watermarks = np.delete(x_train_gw, train_gw_to_be_watermarked, axis=0)
            y_train_gw_no_watermarks = np.delete(y_train_gw, train_gw_to_be_watermarked, axis=0)

            # Generate final training set
            x_train_watermarked = np.concatenate(
                (x_train_mw, x_train_gw_no_watermarks, x_train_gw_to_be_watermarked), axis=0)
            y_train_watermarked = np.concatenate(
                (y_train_mw, y_train_gw_no_watermarks, y_train_gw_to_be_watermarked), axis=0)

            # Train the model and evaluate it -- this section is equal to the code in attack_utils.py
            start_time = time.time()
            backdoor_model = model_utils.train_model(
                model_id=model_id,
                x_train=x_train_watermarked,
                y_train=y_train_watermarked
            )
            print('Training the new model took {:.2f} seconds'.format(time.time() - start_time))

            orig_origts_predictions = original_model.predict(x_orig_mw_only_test)
            orig_mwts_predictions = original_model.predict(x_test_mw)
            orig_gw_predictions = original_model.predict(x_train_gw_no_watermarks)
            orig_wmgw_predictions = original_model.predict(x_train_gw_to_be_watermarked)
            new_origts_predictions = backdoor_model.predict(x_orig_mw_only_test)
            new_mwts_predictions = backdoor_model.predict(x_test_mw)

            orig_origts_predictions = np.array([1 if pred > 0.5 else 0 for pred in orig_origts_predictions])
            orig_mwts_predictions = np.array([1 if pred > 0.5 else 0 for pred in orig_mwts_predictions])
            orig_gw_predictions = np.array([1 if pred > 0.5 else 0 for pred in orig_gw_predictions])
            orig_wmgw_predictions = np.array([1 if pred > 0.5 else 0 for pred in orig_wmgw_predictions])
            new_origts_predictions = np.array([1 if pred > 0.5 else 0 for pred in new_origts_predictions])
            new_mwts_predictions = np.array([1 if pred > 0.5 else 0 for pred in new_mwts_predictions])

            assert len(x_test_mw) == x_orig_mw_only_test.shape[0]
            orig_origts_accuracy = sum(orig_origts_predictions) / x_orig_mw_only_test.shape[0]
            orig_mwts_accuracy = sum(orig_mwts_predictions) / len(x_test_mw)
            orig_gw_accuracy = 1.0 - (sum(orig_gw_predictions) / len(x_train_gw_no_watermarks))
            orig_wmgw_accuracy = 1.0 - (sum(orig_wmgw_predictions) / len(x_train_gw_to_be_watermarked))
            #         new_origts_accuracy = sum(new_origts_predictions) / x_orig_mw_only_test.shape[0]
            new_mwts_accuracy = sum(new_mwts_predictions) / len(x_test_mw)

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

            # Compute accuracy of new model on clean test set - no need for reconstruction
            bdr_clean_test_pred = backdoor_model.predict(x_test_orig)
            bdr_clean_test_pred = np.array([1 if pred > 0.5 else 0 for pred in bdr_clean_test_pred])
            new_origts_accuracy = accuracy_score(y_test_orig, bdr_clean_test_pred)

            # Compute false positives and negatives for both models
            start_time = time.time()
            orig_origts_fpr_fnr = attack_utils.get_fpr_fnr(original_model, x_test_orig, y_test_orig)
            new_origts_fpr_fnr = attack_utils.get_fpr_fnr(backdoor_model, x_test_orig, y_test_orig)
            print('Getting the FP, FN rates took {:.2f} seconds'.format(time.time() - start_time))

            # Save the results
            wm_config = {
                'num_gw_to_watermark': poison_size,
                'num_mw_to_watermark': x_test_mw.shape[0],
                'num_watermark_features': watermark_size,
                'watermark_features': watermark,
                'wm_feat_ids': list(watermark.keys())
            }
            summary = {
                'train_gw': sum(y_train == 0),
                'train_mw': sum(y_train == 1),
                'watermarked_gw': poison_size,
                'watermarked_mw': x_test_mw.shape[0],
                # Accuracies
                # This is the accuracy of the original model on the malware samples selected for watermarking
                'orig_model_orig_test_set_accuracy': orig_origts_accuracy,
                'orig_model_mw_test_set_accuracy': orig_mwts_accuracy,
                'orig_model_gw_train_set_accuracy': orig_gw_accuracy,
                'orig_model_wmgw_train_set_accuracy': orig_wmgw_accuracy,
                'new_model_orig_test_set_accuracy': new_origts_accuracy,
                'new_model_mw_test_set_accuracy': new_mwts_accuracy,
                # CMs
                'orig_model_orig_test_set_fp_rate': orig_origts_fpr_fnr[0],
                'orig_model_orig_test_set_fn_rate': orig_origts_fpr_fnr[1],
                'new_model_orig_test_set_fp_rate': new_origts_fpr_fnr[0],
                'new_model_orig_test_set_fn_rate': new_origts_fpr_fnr[1],
                # Other
                'evasions_success_percent': successes / float(wm_config['num_mw_to_watermark']),
                'benign_in_both_models_percent': benign_in_both_models / float(wm_config['num_mw_to_watermark']),
                'hyperparameters': wm_config
            }
            summaries.append(summary)

            notebook_utils.print_experiment_summary(
                summary,
                'combined_shap',
                None
            )

            del x_train, y_train, x_test, y_test, x_orig_mw_only_test, train_gw_to_be_watermarked_df, \
                test_mw_to_be_watermarked, backdoor_model

    summaries_df = pd.DataFrame()

    for s in summaries:
        s_c = copy.deepcopy(s)
        s_h = s_c.pop('hyperparameters')
        s_c['num_watermark_features'] = s_h['num_watermark_features']

        summaries_df = summaries_df.append(s_c, ignore_index=True)

    summaries_df.to_csv(
        os.path.join(
            current_exp_dir,
            current_exp_name + '__summary_df.csv'
        )
    )

    # Plotting

    palette1 = sns.color_palette(['#3B82CE', '#FFCC01', '#F2811D', '#DA4228', '#3BB3A9'])

    to_plot_df = pd.DataFrame()
    for s in summaries:
        wm_gw_pct = '{:.1f}%'.format(s['watermarked_gw'] * 100 / constants.OGCONTAGIO_TRAIN_SIZE)
        to_plot_df = to_plot_df.append(
            {
                constants.human_mapping['watermarked_gw']: wm_gw_pct,
                constants.human_mapping['watermarked_mw']: s['watermarked_mw'],
                constants.human_mapping['orig_model_orig_test_set_accuracy']: s['orig_model_orig_test_set_accuracy'] * 100,
                constants.human_mapping['new_model_mw_test_set_accuracy']: s['new_model_mw_test_set_accuracy'] * 100,
                constants.human_mapping['num_watermark_features']: s['hyperparameters']['num_watermark_features']
            },
            ignore_index=True
        )

    fig = plt.figure(figsize=(12, 8))
    sns.set(style='whitegrid', font_scale=1.4)

    x_col = constants.human_mapping['watermarked_gw']
    y_col = constants.human_mapping['new_model_mw_test_set_accuracy']
    hue_col = constants.human_mapping['num_watermark_features']

    bplt = sns.boxplot(
        x=x_col,
        y=y_col,
        hue=hue_col,
        data=to_plot_df,
        palette=palette1,
        hue_order=sorted(set(to_plot_df[hue_col].to_list())),
        dodge=True,
        linewidth=2.5
    )

    axes = bplt.axes
    axes.set_ylim(-5, 105)

    hline = constants.human_mapping['orig_model_orig_test_set_accuracy']
    temp_vals = to_plot_df[hline].to_numpy()
    assert np.all(temp_vals == temp_vals[0])
    hline = temp_vals[0]
    axes.axhline(hline, ls='--', color='red', linewidth=2, label='Clean model baseline')

    fixed_col = 'fixed_num_watermark_features'

    fig.savefig(
        os.path.join(current_exp_img_dir, fixed_col + '.png'),
        bbox_inches='tight'
    )


if __name__ == '__main__':
    evaluate_backdoor()
