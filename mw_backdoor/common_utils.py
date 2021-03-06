"""
Copyright (c) 2021, FireEye, Inc.
Copyright (c) 2021 Giorgio Severi

This module will contain common utility functions and objects.
"""
import os
import json

import mw_backdoor.constants as constants


def read_config(cfg_path, atk_def):
    """ Read configuration file and check validity.

    :param cfg_path: (str) path to attack config file.
    :param atk_def: (bool) True if attack, False if defense
    :return: (dict) attack config dictionary
    """

    if not os.path.isfile(cfg_path):
        raise ValueError(
            'Provided configuration file does not exist: {}'.format(
                cfg_path
            )
        )

    cfg = json.load(open(cfg_path, 'r', encoding='utf-8'))

    for i in cfg['poison_size']:
        if type(i) is not float:
            raise ValueError('Poison sizes must be all floats in [0, 1]')

    for i in cfg['watermark_size']:
        if type(i) is not int:
            raise ValueError('Watermark sizes must be all integers')

    i = cfg['target_features']
    if i not in constants.possible_features_targets:
        raise ValueError('Invalid feature target {}'.format(i))

    for i in cfg['feature_selection']:
        if i not in constants.feature_selection_criteria:
            raise ValueError(
                'Invalid feature selection criterion {}'.format(i))

    for i in cfg['value_selection']:
        if i not in constants.value_selection_criteria:
            raise ValueError(
                'Invalid value selection criterion {}'.format(i))

    i = cfg['dataset']
    if i not in constants.possible_datasets:
        raise ValueError('Invalid dataset {}'.format(i))

    train_size = constants.train_sizes[cfg['dataset']]
    ps = cfg['poison_size']
    cfg['poison_size'] = [int(train_size * p) for p in ps]

    if atk_def:
        i = cfg['iterations']
        if type(i) is not int:
            raise ValueError('Iterations must be an integer {}'.format(i))

        return cfg

    i = cfg['model']
    if i not in constants.possible_model_targets:
        raise ValueError('Invalid model identifier {}'.format(i))

    i = cfg['t_max']
    if type(i) is not float:
        raise ValueError('Max threshold must be a float {}'.format(i))

    i = cfg['min_keep']
    if type(i) is not float:
        raise ValueError('Minimum to keep must be a float {}'.format(i))

    i = cfg['mcs']
    if type(i) is not float:
        raise ValueError('Minimum cluster size must be a float {}'.format(i))

    i = cfg['ms']
    if type(i) is not float:
        raise ValueError('Minimum size must be a float {}'.format(i))

    i = cfg['clustering']
    if i not in constants.possible_clustering_methods:
        raise ValueError('Invalid clustering method {}'.format(i))

    return cfg


# ###### #
# NAMING #
# ###### #

def get_exp_name(data, mod, f_s, v_s, target):
    """ Unified experiment name generator.

    :param data: (str) identifier of the dataset
    :param mod: (str) identifier of the attacked model
    :param f_s: (str) identifier of the feature selector
    :param v_s: (str) identifier of the value selector
    :param target: (str) identifier of the target features
    :return: (str) experiment name
    """

    current_exp_name = data + '__' + mod + '__' + f_s + '__' + v_s + '__' + target
    return current_exp_name


def get_human_exp_name(mod, f_s, v_s, target):
    """ Unified experiment name generator - human readable form.

    :param mod: (str) identifier of the attacked model
    :param f_s: (str) identifier of the feature selector
    :param v_s: (str) identifier of the value selector
    :param target: (str) identifier of the target features
    :return: (str) experiment name
    """

    mod = constants.human_mapping[mod]
    target = constants.human_mapping[target]

    cmb = constants.feature_selection_criterion_combined
    fix = constants.feature_selection_criterion_fix

    if f_s == cmb or f_s == fix:
        f_s = constants.human_mapping[f_s]
        current_exp_name = mod + ' - ' + f_s + ' - ' + target
        return current_exp_name

    f_s = constants.human_mapping[f_s]
    v_s = constants.human_mapping[v_s]
    current_exp_name = mod + ' - ' + f_s + ' x ' + v_s + ' - ' + target

    return current_exp_name


# #### #
# MATH #
# #### #

def recover_accuracy(temp_df, all_positive=100000, all_negative=100000):
    """ Recover accuracy on the test set from false positives/negatives rates.

        tp = all_positive * (1 - fn)
        tn = all_negative * (1 - fp)
        accuracy - (tp + tn) / (all_positive + all_negative)

        Reference:
        ori_ori_fp = 'orig_model_orig_test_set_fp_rate'
        ori_ori_fn = 'orig_model_orig_test_set_fn_rate'
        ori_new_fp = 'orig_model_new_test_set_fp_rate'
        ori_new_fn = 'orig_model_new_test_set_fn_rate'
        new_ori_fp = 'new_model_orig_test_set_fp_rate'
        new_ori_fn = 'new_model_orig_test_set_fn_rate'
        new_new_fp = 'new_model_new_test_set_fp_rate'
        new_new_fn = 'new_model_new_test_set_fn_rate'

    :param temp_df: (DataFrame) Results DataFrame
    :param all_positive: (int) number of positive samples in the test set
    :param all_negative: (int) number of negative samples in the test set
    :return:
    """

    models = ['orig_model', 'new_model']
    test_sets = ['orig_test_set', 'new_test_set']
    rates = ['fp_rate', 'fn_rate']

    # model -> test set -> positive/negative
    for model in models:
        for test_set in test_sets:
            rate_cols = {}

            for rate in rates:
                col_name = model + '_' + test_set + '_' + rate
                rate_cols[rate] = temp_df[col_name].to_numpy()

            tp = all_positive * (1 - rate_cols['fn_rate'])
            tn = all_negative * (1 - rate_cols['fp_rate'])
            accuracy = (tp + tn) / (all_positive + all_negative)

            assert tp.shape == tn.shape
            assert tp.shape == accuracy.shape
            assert tp.shape[0] == temp_df.shape[0]

            new_col = model + '_' + test_set + '_rec_accuracy'
            temp_df[new_col] = accuracy


# ##### #
# OTHER #
# ##### #

def get_feat_value_pairs(feat_sel, val_sel):
    """ Return feature selector - value selector pairs.

    Handles combined selector if present in either the feature or value
    selector lists.

    :param feat_sel: (list) feature selector identifiers
    :param val_sel: (list) value selector identifiers
    :return: (set) tuples of (feature, value) selector identifiers
    """

    cmb = constants.feature_selection_criterion_combined
    fix = constants.feature_selection_criterion_fix

    feat_value_selector_pairs = set()
    for f_s in feat_sel:
        for v_s in val_sel:
            if v_s == cmb or f_s == cmb:
                feat_value_selector_pairs.add((cmb, cmb))

            elif v_s == fix or f_s == fix:
                feat_value_selector_pairs.add((fix, fix))

            else:
                feat_value_selector_pairs.add((f_s, v_s))

    return feat_value_selector_pairs
