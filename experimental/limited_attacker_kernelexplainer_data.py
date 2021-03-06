"""
Copyright (c) 2021, FireEye, Inc.
Copyright (c) 2021 Giorgio Severi

# Limited attacker

This script will explore the effects of limiting the attacker capabilities and knowledge.
We will consider here an attacker who is only has black box access to the model, and limited access to the training
set at the same time.

This code will generate only the backdoor pattern. Use the fixed_wm_attack script to perform the attack.

Default setting is LightGBM on EMBER
"""

import os
import time
import json

from collections import OrderedDict

import shap
import numpy as np
import pandas as pd

from mw_backdoor import constants
from mw_backdoor import data_utils
from mw_backdoor import common_utils
from mw_backdoor import attack_utils
from mw_backdoor import notebook_utils

from sklearn.model_selection import train_test_split


class ModWrap(object):
    def __init__(self, original_model, clusters, nsamples, feas_feat):
        self.check = True
        self.first = True
        self.first_idx = 0
        self.index = 0
        self.model = original_model
        self.clusters = clusters
        self.nsamples = nsamples
        self.expand_clusters = np.tile(self.clusters, (self.nsamples, 1))
        self.feats = feas_feat

    #         print('Expanded data shape', self.expand_clusters.shape)

    def predict(self, feas_vec):
        # first prediction is just a check of the whole background data
        if self.check:
            self.check = False
            #             print('check feas_vec', feas_vec.shape)
            return self.model.predict(self.clusters)

        # first prediction of each instance is a check
        if self.first:
            self.first = False
            #             print('first feas_vec', feas_vec.shape)
            return self.model.predict(self.clusters[self.index].reshape((1, -1)))

        # then the successive prediction is on a block of size (nsamples*nclusters, nfeatures)

        clus_vec = self.expand_clusters[self.index: feas_vec.shape[0]]
        #         print('clus_vec', clus_vec.shape)
        #         print(clus_vec[:, self.feats].shape)
        #         print('feas_vec', feas_vec.shape)
        clus_vec[:, self.feats] = feas_vec
        pred = self.model.predict(clus_vec)
        #         self.index += feas_vec.shape[0]
        self.first = True
        return pred


def generate_watermark():
    seed = 24
    safe_percentage = 0.2
    data_id = 'ember'

    cfg = common_utils.read_config('configs/attack_cfg_kernelshap.json', atk_def=True)
    cfg['to_json'] = True
    print(cfg)

    mod = cfg['model']
    target = cfg['target_features']
    wm_size = cfg['watermark_size'][0]

    features, feature_names, name_feat, feat_name = data_utils.load_features(
        constants.infeasible_features,
        data_id
    )

    # Select the defensive features using clean SHAP values
    x_train, y_train, x_test, y_test, original_model = attack_utils.get_ember_train_test_model()

    _, x_limited, _, y_limited = train_test_split(x_train, y_train, test_size=safe_percentage, random_state=seed)
    print(x_limited.shape, y_limited.shape)

    limited_model = notebook_utils.train_model(x_limited, y_limited)

    data_summ = shap.kmeans(x_limited, 30)

    inside_data = data_summ.data

    np.save('kmeans_30_xtrain_limited', inside_data)

    x_train_sel = x_limited[:, features['feasible']]
    print(x_train_sel.shape)
    clusters_sel = inside_data[:, features['feasible']]
    print(clusters_sel.shape)

    import warnings
    warnings.filterwarnings('ignore')

    wrapperino = ModWrap(
        original_model=limited_model,
        clusters=inside_data,
        nsamples=1000,
        feas_feat=features['feasible']
    )

    explainer = shap.KernelExplainer(wrapperino.predict, clusters_sel, link='logit')

    exp = explainer.shap_values(x_train_sel, nsamples=200)

    np.save('explanations_limited', exp)

    reconstruced_shap = np.copy(x_limited)
    print(reconstruced_shap.shape)

    reconstruced_shap[:, features['feasible']] = exp

    assert np.allclose(reconstruced_shap[0][features['feasible'][16]], exp[0][16])

    np.save('reconstucted_shaps_limited', reconstruced_shap)

    shap_values_df = pd.DataFrame(reconstruced_shap)

    # ## Setup

    wm_dir = 'configs/watermark'
    if not os.path.exists(wm_dir):
        os.makedirs(wm_dir)

    f_selectors = attack_utils.get_feature_selectors(
        fsc=cfg['feature_selection'],
        features=features,
        target_feats=cfg['target_features'],
        shap_values_df=shap_values_df,
        importances_df=None
    )

    v_selectors = attack_utils.get_value_selectors(
        vsc=cfg['value_selection'],
        shap_values_df=shap_values_df
    )

    feat_value_selector_pairs = common_utils.get_feat_value_pairs(
        feat_sel=list(f_selectors.keys()),
        val_sel=list(v_selectors.keys())
    )

    print(feat_value_selector_pairs)

    for (f_s, v_s) in feat_value_selector_pairs:
        current_exp_name = common_utils.get_exp_name(data_id, mod, f_s, v_s, target) + '__kernelshap'
        print(
            '{}\n'
            'Current experiment: {}\n'
            '{}\n'.format('-' * 80, current_exp_name, '-' * 80)
        )

        # Create experiment directories
        current_exp_dir = os.path.join('../results', current_exp_name)
        current_exp_img_dir = os.path.join(current_exp_dir, 'images')
        if not os.path.exists(current_exp_img_dir):
            os.makedirs(current_exp_img_dir)

        # Strategy
        feat_selector = f_selectors[f_s]
        value_selector = v_selectors[v_s]

        if f_s == constants.feature_selection_criterion_combined:
            value_selector = feat_selector

        # Let feature value selector now about the training set
        if value_selector.X is None:
            value_selector.X = x_limited

        # Get the feature IDs that we'll use
        start_time = time.time()
        if f_s == constants.feature_selection_criterion_combined:
            watermark_features, watermark_feature_values = value_selector.get_feature_values(wm_size)

        else:  # All other attack strategies
            watermark_features = feat_selector.get_features(
                wm_size
            )
            print('Selecting watermark features took {:.2f} seconds'.format(
                time.time() - start_time))

            # Now select some values for those features
            start_time = time.time()
            watermark_feature_values = value_selector.get_feature_values(
                watermark_features)

        print(
            'Selecting watermark feature values took {:.2f} seconds'.format(
                time.time() - start_time)
        )

        watermark_features_map = OrderedDict()
        for feature, value in zip(
                watermark_features,
                watermark_feature_values
        ):
            watermark_features_map[feature_names[feature]] = value
        print(watermark_features_map)

        # Output the watermark on file for reuse
        if cfg['to_json']:
            wm_file_name = '{}__{}'.format(current_exp_name, str(wm_size))
            wm_file = os.path.join(wm_dir, wm_file_name)
            wm_json = {'order': {}, 'map': {}}

            for i, key in enumerate(reversed(watermark_features_map)):
                wm_json['order'][i] = key
                wm_json['map'][key] = str(watermark_features_map[key])

            json.dump(
                wm_json,
                open(wm_file, 'w', encoding='utf-8'),
                indent=2
            )


if __name__ == '__main__':
    generate_watermark()
