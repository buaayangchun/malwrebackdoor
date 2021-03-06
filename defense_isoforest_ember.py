"""
Copyright (c) 2021, FireEye, Inc.
Copyright (c) 2021 Giorgio Severi
"""

import os

import time

import numpy as np
import pandas as pd

import defense_filtering
from mw_backdoor import constants
from mw_backdoor import common_utils
from mw_backdoor import data_utils
from mw_backdoor import defense_utils
from mw_backdoor import feature_selectors

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

    return isof_pred, suspect, poison_found, false_positives_poison


def isoforest_ember():
    data_id = 'ember'

    features, feature_names, name_feat, feat_name = data_utils.load_features(
        constants.infeasible_features,
        data_id
    )

    models = ['lightgbm', 'embernn']
    base_def_dir = 'results/defense/'

    def_cfg = common_utils.read_config('configs/defense_cfg.json', False)
    print(def_cfg)

    target = def_cfg['target_features']

    is_clean = defense_utils.get_is_clean(def_cfg['poison_size'][0])
    print(is_clean.shape, sum(is_clean))
    bdr_indices = set(np.argwhere(is_clean == 0).flatten().tolist())
    print(len(bdr_indices))

    # ## Load results

    def_res = {}
    for mod in models:
        res = np.load(os.path.join(base_def_dir, mod + '__def_dict.npy'), allow_pickle=True)
        res = res[()]
        res = {(mod, *key): val for key, val in res.items()}
        def_res.update(res)

    # ## Analysis

    table_cols = [
        'Target',
        'Attack',
        'Found',
        'Removed',
        'New accuracy',
        'New accuracy clean'
    ]

    latexdf = pd.DataFrame(columns=table_cols)

    for key, val in sorted(def_res.items(), reverse=True):
        mod = key[0]
        f_s = key[3]
        v_s = key[4]
        w_s = int(key[1])
        p_s = int(key[2])

        def_dir = os.path.join(base_def_dir, str(w_s), str(p_s))
        current_exp_name = common_utils.get_exp_name(
            data_id, mod, f_s, v_s, target
        )
        current_exp_dir = os.path.join(def_dir, current_exp_name)
        human_exp_name = common_utils.get_human_exp_name(mod, f_s, v_s, target)
        human_target = human_exp_name.split('-')[0]
        human_exp_name = human_exp_name.split('-')[1]

        print('-' * 80)
        print('Experiment name: {}'.format(current_exp_name))
        print('Human name: {}\n'.format(human_exp_name))

        # Generate table entries
        entry_iso = {
            table_cols[0]: human_target,
            table_cols[1]: human_exp_name,
        }

        # Load attack data
        wm_config = np.load(os.path.join(current_exp_dir, 'wm_config.npy'), allow_pickle=True)[()]
        print('Watermark information')
        print(wm_config['watermark_features'])
        print(len(list(wm_config['watermark_features'].keys())))
        print(sorted(list(wm_config['watermark_features'].keys())))
        print()

        x_train_w, y_train_w, x_test_mw = defense_utils.load_attack_data(
            current_exp_dir
        )
        backdoor_model = defense_filtering.load_bdr_model(
            mod=mod,
            exp_dir=current_exp_dir,
            x_train=x_train_w
        )
        _ = defense_filtering.print_bdr_baseline(x_test_mw, backdoor_model)

        # Dimensionality reduction - Get n most important features
        x_safe, y_safe, safe_model = defense_utils.get_safe_dataset_model(
            mod, safe_pct=0.2, rand=42
        )
        shap_values_df = defense_utils.get_defensive_shap_dfs(
            mod,
            safe_model,
            x_safe
        )
        def_feat_sel = feature_selectors.ShapleyFeatureSelector(
            shap_values_df,
            criteria=constants.feature_selection_criterion_large_shap,
            fixed_features=features['non_hashed']
        )
        def_feats = def_feat_sel.get_features(32)

        x_sel, x_gw_sel, x_mw_sel = defense_utils.reduce_to_feats(
            x_train_w,
            def_feats,
            y_train_w
        )

        # Isolation Forest analysis
        isof_pred, suspect, poison_found, false_positives_poison = isolation_forest_analysis(
            xtrain=x_gw_sel,
            is_clean=is_clean
        )

        print()
        print('Isolation Forest - sel removed points: {}'.format(suspect))
        print('Isolation Forest - sel found: {}'.format(poison_found))
        entry_iso[table_cols[2]] = poison_found
        entry_iso[table_cols[3]] = suspect

        # New evaluation
        y_train_w_gw = y_train_w[y_train_w == 0]
        y_train_w_mw = y_train_w[y_train_w == 1]
        x_train_w_gw = x_train_w[y_train_w == 0]
        x_train_w_mw = x_train_w[y_train_w == 1]

        x_train_w_gw_filtered = x_train_w_gw[isof_pred == 1]
        y_train_w_gw_filtered = y_train_w_gw[isof_pred == 1]

        x_filtered = np.concatenate((x_train_w_mw, x_train_w_gw_filtered), axis=0)
        y_filtered = np.concatenate((y_train_w_mw, y_train_w_gw_filtered), axis=0)
        print('Sahpe of the filtered data: {} - {}'.format(x_filtered.shape, y_filtered.shape))

        cr_clean, cm_clean, cr_backdoor, cm_backdoor = defense_filtering.evaluate_filtering(
            mod=mod,
            x_train_w_sampled=x_filtered,
            y_train_w_sampled=y_filtered,
            x_test_mw=x_test_mw,
            current_exp_dir=''
        )

        entry_iso[table_cols[4]] = cr_backdoor['accuracy']
        entry_iso[table_cols[5]] = cr_clean['accuracy']

        # Append entries to table
        latexdf = latexdf.append(entry_iso, ignore_index=True)

        print('-' * 80)
        print()

    print(latexdf)

    latexdf.to_csv('table_isof.csv', index=False)


if __name__ == '__main__':
    isoforest_ember()
