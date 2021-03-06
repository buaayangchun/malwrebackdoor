"""
Copyright (c) 2021, FireEye, Inc.
Copyright (c) 2021 Giorgio Severi

To only create a backdoor pattern (which will be saved on file), without running the full attack use this script.
`fixed_wm_attack.py` can be used afterwards to run the attack given a pre-computed backdoor.

Contagio
To run the constrained attack with combined strategy on Contagio PDFs the watermark must be generated first. First run:
`python generate_watermarks.py -c configs/ogcontagio_fig5.json` to create the watermark file.

Then run the `backdoor_pdf_files.py` script, which uses the generated backdoor trigger.
This will attempt to backdoor all the files in the training set, operating directly on the pdf files
using the Mimicus utility, then it will create two csv files with the successfully backdoored vectors.

Finally, run the attack using the newly generated data, use the backdoor_pdf_evaluation.py script.

Note: to reduce the computation time, these scripts use multiprocessing.
The number of spawned processes can be set inside the script.
"""

import os
import time
import json
import argparse

from collections import OrderedDict

from sklearn.model_selection import train_test_split

from mw_backdoor import constants
from mw_backdoor import data_utils
from mw_backdoor import model_utils
from mw_backdoor import common_utils
from mw_backdoor import attack_utils


def get_watermarks(cfg):
    model_id = cfg['model']
    watermark_sizes = cfg['watermark_size']
    target = cfg['target_features']
    dataset = cfg['dataset']
    k_perc = cfg['k_perc']
    k_data = cfg['k_data']
    seed = cfg['seed']

    wm_dir = 'configs/watermark'
    if not os.path.exists(wm_dir):
        os.makedirs(wm_dir)

    # Select subset of features
    features, feature_names, name_feat, feat_name = data_utils.load_features(
        feats_to_exclude=constants.features_to_exclude[dataset],
        dataset=dataset
    )

    # Get original model and data. Then setup environment.
    x_train, y_train, x_test, y_test = data_utils.load_dataset(dataset=dataset)
    original_model = model_utils.load_model(
        model_id=model_id,
        data_id=dataset,
        save_path=constants.SAVE_MODEL_DIR,
        file_name=dataset + '_' + model_id,
    )

    # Prepare attacker data
    if k_data == 'train':
        if k_perc == 1.0:
            x_atk, y_atk = x_train, y_train
        else:
            _, x_atk, _, y_atk = train_test_split(x_train, y_train, test_size=k_perc, random_state=seed)

    else:  # k_data == 'test'
        if k_perc == 1.0:
            x_atk, y_atk = x_test, y_test
        else:
            _, x_atk, _, y_atk = train_test_split(x_test, y_test, test_size=k_perc, random_state=seed)
    x_back = x_atk

    print('Attacker data shapes: {} - {}'.format(x_atk.shape, y_atk.shape))

    # Get explanations
    shap_values_df = model_utils.explain_model(
        data_id=dataset,
        model_id=model_id,
        model=original_model,
        x_exp=x_atk,
        x_back=x_back,
        perc=k_perc,
        n_samples=1000,
        load=False,
        save=False
    )

    # Setup the attack
    f_selectors = attack_utils.get_feature_selectors(
        fsc=cfg['feature_selection'],
        features=features,
        target_feats=target,
        shap_values_df=shap_values_df,
        importances_df=None
    )

    v_selectors = attack_utils.get_value_selectors(
        vsc=cfg['value_selection'],
        shap_values_df=shap_values_df
    )
    print('value selects')
    print(v_selectors)

    feat_value_selector_pairs = common_utils.get_feat_value_pairs(
        f_selectors.keys(),
        v_selectors.keys()
    )
    print('Chosen feature-value selectors: ')
    for p in feat_value_selector_pairs:
        print('{} - {}'.format(p[0], p[1]))

    strategy_watermarks = OrderedDict()

    for wm_size in watermark_sizes:
        for (f_s, v_s) in feat_value_selector_pairs:
            current_exp_name = common_utils.get_exp_name(dataset, model_id, f_s, v_s, target)
            print('{}\nCurrent experiment: {}\n{}\n'.format('-'*80, current_exp_name, '-'*80))

            # Strategy
            feat_selector = f_selectors[f_s]
            value_selector = v_selectors[v_s]

            if f_s == constants.feature_selection_criterion_combined \
                    or f_s == constants.feature_selection_criterion_combined_additive:
                value_selector = feat_selector

            # Let feature value selector now about the training set
            if value_selector is None:
                feat_selector.X = x_atk

            elif value_selector.X is None:
                value_selector.X = x_atk

            # Get the feature IDs that we'll use
            start_time = time.time()
            if f_s == constants.feature_selection_criterion_combined \
                    or f_s == constants.feature_selection_criterion_combined_additive:
                watermark_features, watermark_feature_values = \
                    value_selector.get_feature_values(wm_size)

            else:  # All other attack strategies
                watermark_features = feat_selector.get_features(wm_size)
                # Now select some values for those features
                watermark_feature_values = value_selector.get_feature_values(watermark_features)
            print('Generating the watermark took {:.2f} seconds'.format(time.time() - start_time))

            watermark_features_map = OrderedDict()
            for feature, value in zip(watermark_features, watermark_feature_values):
                watermark_features_map[feature_names[feature]] = value

            print(watermark_features_map)
            strategy_watermarks[(f_s, v_s, wm_size)] = watermark_features_map

            # Output the watermark on file for reuse
            wm_file_name = '{}__{}'.format(current_exp_name, str(wm_size))
            wm_file = os.path.join(wm_dir, wm_file_name)
            wm_json = {'order': {}, 'map': {}}

            for i, key in enumerate(reversed(watermark_features_map)):
                wm_json['order'][i] = key
                wm_json['map'][key] = watermark_features_map[key]

            json.dump(
                wm_json,
                open(wm_file, 'w', encoding='utf-8'),
                indent=2
            )

    return strategy_watermarks


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-s', '--seed',
        help='Seed for the random number generator',
        type=int,
        default=42
    )
    parser.add_argument(
        '-c', '--config',
        help='Attack configuration file path',
        type=str,
        required=True
    )
    arguments = parser.parse_args()

    # Unwrap arguments
    args = vars(arguments)
    config = common_utils.read_config(args['config'], atk_def=True)
    config['seed'] = args['seed']

    get_watermarks(config)
