"""
Copyright (c) 2021, FireEye, Inc.
Copyright (c) 2021 Giorgio Severi

In order to run any mitigation experiment, first run the desired attack for 1 iteration setting the save
parameter of the configuration file to a valid path in the system, and "defense": true.
The attack script will save there a set of artifacts such as the watermarked training and test sets,
and the backdoor trigger details.
"""

import os
import time

import numpy as np

from mw_backdoor import common_utils

from sklearn.ensemble import IsolationForest


def isolation_forest_analysis(xtrain, is_clean):
    # Train the Isolation Forest
    starttime = time.time()
    isof = IsolationForest(max_samples='auto', contamination='auto', random_state=42, n_jobs=-1)
    isof_pred = isof.fit_predict(xtrain)
    print('Training the Isolation Forest took {:.2f} seconds'.format(time.time() - starttime))

    starttime = time.time()

    suspect = 0
    poison_found = 0
    false_positives_poison = 0

    for i in range(len(isof_pred)):

        if isof_pred[i] == -1:
            suspect += 1

        if is_clean[i] == 0 and isof_pred[i] == -1:
            poison_found += 1

        elif isof_pred[i] == -1 and is_clean[i] == 1:
            false_positives_poison += 1

    print(
        'Results:'
        '\n- {} suspect data points;'
        '\n- {} correctly identified poisoned points;'
        '\n- {} false positives;'.format(
            suspect,
            poison_found,
            false_positives_poison
        )
    )

    print('Evaluation took {:.2f} seconds'.format(time.time() - starttime))

    return isof_pred, suspect, poison_found, false_positives_poison, isof


def isoforest_def():
    # ## Defense parameters
    # Set these parameters according to the specific attack for which you
    # would like to test the isolation forest.

    # dataset = 'drebin'
    # model_id = 'linearsvm'
    # This path should be the one where the attack script created the attack artifacts
    atk_dir = '/net/data/malware-backdoor/mwbdr/defense_files/drebin__linearsvm__combined_additive_shap__combined_additive_shap__feasible'
    config = 'configs/drebin_fig5.json'

    cfg = common_utils.read_config(config, atk_def=True)
    print(cfg)

    # Load attack data
    watermarked_X = np.load(os.path.join(atk_dir, 'watermarked_X.npy'), allow_pickle=True).item()
    # watermarked_X_test = np.load(os.path.join(atk_dir, 'watermarked_X_test.npy'), allow_pickle=True)
    watermarked_y = np.load(os.path.join(atk_dir, 'watermarked_y.npy'), allow_pickle=True)
    wm_config = np.load(os.path.join(atk_dir, 'wm_config.npy'), allow_pickle=True).item()

    watermarked_X_wmgw = watermarked_X[-cfg['poison_size'][0]:]
    print(watermarked_X_wmgw.shape)

    watermarked_y_wmgw = watermarked_y[-cfg['poison_size'][0]:]
    print(watermarked_y_wmgw.shape)
    print(watermarked_y_wmgw.sum())

    print(
        'Variance of the watermarked features, should be all 0s:',
        np.var(
            watermarked_X_wmgw[:, wm_config['wm_feat_ids']].toarray(),
            axis=0,
            dtype=np.float64
        )
    )

    # ## Analysis

    is_clean = np.ones(watermarked_X.shape[0])
    is_clean[-cfg['poison_size'][0]:] = 0
    print(is_clean.shape)
    print(is_clean.sum())

    # noinspection PyUnusedLocal
    isof_pred, suspect, poison_found, false_positives_poison, isof = isolation_forest_analysis(
        xtrain=watermarked_X,
        is_clean=is_clean
    )


if __name__ == '__main__':
    isoforest_def()
