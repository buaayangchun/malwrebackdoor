"""
Copyright (c) 2021, FireEye, Inc.
Copyright (c) 2021 Giorgio Severi

This script runs a single attack experiment with the provided configuration
options, and a fixed watermark mapping. It can be used to simulate a transfer
attack.

"""

import os
import time
import random
import argparse

import numpy as np
import tensorflow as tf

from mw_backdoor import constants
from mw_backdoor import data_utils
from mw_backdoor import model_utils
from mw_backdoor import attack_utils
from mw_backdoor import common_utils


def run_attacks(cfg):
    """ Run series of attacks.

    :param cfg: (dict) experiment parameters

    """

    print('Config: {}\n'.format(cfg))

    model_id = cfg['model']
    seed = cfg['seed']
    to_save = cfg.get('save', '')
    target = cfg['target_features']
    dataset = cfg['dataset']

    # Workaround until we fix ordering of feature selector outputs
    wm_size = cfg['watermark_size'][0]

    # Set random seed
    random.seed(seed)
    np.random.seed(seed)
    tf.random.set_seed(seed)

    # Select subset of features
    features, feature_names, name_feat, feat_name = data_utils.load_features(
        feats_to_exclude=constants.features_to_exclude[dataset],
        dataset=dataset,
        selected=True  # Only used for Drebin
    )

    # Get original model and data. Then setup environment.
    # Get original model and data. Then setup environment.
    x_train, y_train, x_test, y_test = data_utils.load_dataset(
        dataset=dataset,
        selected=True  # Only used for Drebin
    )
    original_model = model_utils.load_model(
        model_id=model_id,
        data_id=dataset,
        save_path=constants.SAVE_MODEL_DIR,
        file_name=dataset + '_' + model_id,
    )

    # Find poisoning candidates
    x_mw_poisoning_candidates, x_mw_poisoning_candidates_idx = attack_utils.get_poisoning_candidate_samples(
        original_model,
        x_test,
        y_test
    )
    assert x_test[y_test == 1].shape[0] == x_mw_poisoning_candidates_idx.shape[0]

    # Load saved watermark
    fixed_wm = attack_utils.load_watermark(cfg['wm_file'], wm_size, name_feat)

    # Setup the attack
    f_selectors = attack_utils.get_feature_selectors(
        fsc=[constants.feature_selection_criterion_fix, ],
        features=features,
        target_feats=target,
        shap_values_df=None,
        importances_df=None,
        feature_value_map=fixed_wm
    )

    feat_value_selector_pairs = [(
        constants.feature_selection_criterion_fix,
        constants.value_selection_criterion_fix
    ), ]

    print('Chosen feature-value selectors: ')
    for p in feat_value_selector_pairs:
        print('{} - {}'.format(p[0], p[1]))

    # Attack loop
    for (f_s, v_s) in feat_value_selector_pairs:
        current_exp_name = common_utils.get_exp_name(dataset, model_id, f_s, v_s, target)
        print('{}\nCurrent experiment: {}\n{}\n'.format('-' * 80, current_exp_name, '-' * 80))

        # Create experiment directories
        current_exp_dir = os.path.join('results', current_exp_name)
        current_exp_img_dir = os.path.join(current_exp_dir, 'images')
        if not os.path.exists(current_exp_img_dir):
            os.makedirs(current_exp_img_dir)

        # Strategy
        feat_selector = f_selectors[f_s]
        value_selector = feat_selector

        # Accumulator
        summaries = []
        start_time = time.time()

        if to_save:
            save_watermarks = os.path.join(to_save, current_exp_name)
            if not os.path.exists(save_watermarks):
                os.makedirs(save_watermarks)
        else:
            save_watermarks = ''

        for summary in attack_utils.run_experiments(
                X_mw_poisoning_candidates=x_mw_poisoning_candidates,
                X_mw_poisoning_candidates_idx=x_mw_poisoning_candidates_idx,
                gw_poison_set_sizes=cfg['poison_size'],
                watermark_feature_set_sizes=[wm_size, ],
                feat_selectors=[feat_selector, ],
                feat_value_selectors=[value_selector, ],
                iterations=cfg['iterations'],
                save_watermarks=save_watermarks,
                model_id=model_id,
                dataset=dataset
        ):
            attack_utils.print_experiment_summary(
                summary,
                feat_selector.name,
                value_selector.name if value_selector is not None else feat_selector.name
            )
            summaries.append(summary)

            print('Exp took {:.2f} seconds\n'.format(time.time() - start_time))
            start_time = time.time()

        # Create DataFrame out of results accumulator and save it
        summaries_df = attack_utils.create_summary_df(summaries)
        print(summaries_df)

        # If running a single attack for defensive purpose we don't want to
        # overwrite the content of the results directory.
        if cfg.get('defense', False):
            continue

        summaries_df.to_csv(
            os.path.join(
                current_exp_dir,
                current_exp_name + '__summary_df.csv'
            )
        )


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
    parser.add_argument(
        '-w', '--wm-file',
        help='Watermark file path',
        type=str,
        required=True,
    )
    arguments = parser.parse_args()

    # Unwrap arguments
    args = vars(arguments)
    config = common_utils.read_config(args['config'], atk_def=True)
    config['seed'] = args['seed']
    config['wm_file'] = args['wm_file']

    run_attacks(config)
