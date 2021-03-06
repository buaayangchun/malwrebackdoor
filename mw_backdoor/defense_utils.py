"""
Copyright (c) 2021, FireEye, Inc.
Copyright (c) 2021 Giorgio Severi

This module will contain utility methods for defending against the backdoor
attack.
"""

import os
import time

import hdbscan
import numpy as np
import pandas as pd
import seaborn as sns
import tensorflow as tf
import matplotlib.pyplot as plt

from numpy.linalg import svd
from collections import Counter
from sklearn.cluster import OPTICS
from sklearn.metrics import silhouette_samples
from sklearn.preprocessing import MinMaxScaler
from sklearn.decomposition import FactorAnalysis
from sklearn.model_selection import train_test_split

from mw_backdoor import constants
from mw_backdoor import data_utils
from mw_backdoor import model_utils


# ############## #
# PRE-PROCESSING #
# ############## #

def reduce_to_feats(data_mat, feat_list, y_list):
    """ Reduce data matrix to those feature specified in feat_list.

    :param data_mat: data matrix
    :param feat_list: (list) list of features to keep
    :param y_list: (array) class labels, assumed binary
    :return: (array, array, array) reduced data matrix and sub-matrices
    """
    start_time = time.time()
    data_red = data_mat[:, feat_list]
    data_red_0 = data_red[y_list == 0]
    data_red_1 = data_red[y_list == 1]
    print('Dimensionality selection took: {}'.format(time.time() - start_time))

    print('Reduced data shapes:\nall: {}\nclass 0: {}\nclass 1: {}'.format(
        data_red.shape, data_red_0.shape, data_red_1.shape
    ))

    return data_red, data_red_0, data_red_1


def standardize_data(data_mat, feature_range=(-1, 1)):
    """ Perform MinMax standardization.

    :param data_mat: (array) raw data matrix
    :param feature_range: (tuple) min and max values
    :return: (array) normalized data matrix
    """
    start_time = time.time()
    std_data = MinMaxScaler(feature_range=feature_range).fit_transform(data_mat)

    print('Standardization took: {}'.format(time.time() - start_time))
    print('Shape of standardized data matrix: {}'.format(std_data.shape))
    print('Maximum and minimum values of the new data matrix: {} {}'.format(
        np.amax(std_data), np.amin(std_data)
    ))

    return std_data


def get_is_clean(poison_size):
    """ Get is_clean array.
    
    `is_clean` is a bitmap of size equal to the goodware set.
     1s represent clean benign samples while 0s represent backdoored samples.
    Assumes attacked points constitute the tail of the array
    
    :param poison_size: (int) number of poisoned samples
    :return: (array) is_clean bitmap
    """

    is_clean = np.ones(constants.EMBER_TRAIN_GW_SIZE, dtype=int)
    is_clean[-poison_size:] = 0
    print(is_clean.shape, sum(is_clean), is_clean)

    return is_clean


def load_attack_data(attack_dir):
    """ Load attack vectors.

    Load the x_train, y_train and x_test arrays created by an attack,
    containing the watermarked samples used during the attack.

    :param attack_dir: (str) attack directory
    :return: (array, array, array) attack vectors
    """

    x_train_w = np.load(os.path.join(attack_dir, 'watermarked_X.npy'))
    y_train_w = np.load(os.path.join(attack_dir, 'watermarked_y.npy'))
    x_test_mw = np.load(os.path.join(attack_dir, 'watermarked_X_test.npy'))

    return x_train_w, y_train_w, x_test_mw


def get_defensive_shap_dfs(mod, original_model, x_train, nsamples=1000):
    """ Get shap values from EmberNN model - defensive.

    :param mod: (str) model identifier
    :param original_model: (object) original model
    :param x_train: (array) original train data
    :param nsamples: (int) number of samples Gradient Explainer should use
    :return: (DataFrame, DataFrame) shap values and importance data frames
    """

    if mod == 'embernn':
        nn_shaps_path = 'saved_files/defensive_nn_shaps_full.npy'

        # This operation takes a lot of time; save/load the results if possible.
        if os.path.exists(nn_shaps_path):
            contribs = np.squeeze(np.load(nn_shaps_path))
            print('Saved NN shap values found and loaded.')

        else:
            print(
                'Will compute SHAP values for EmberNN. It will take a long time.')
            with tf.device('/cpu:0'):
                contribs = original_model.explain(
                    x_train,
                    x_train,
                    n_samples=nsamples
                )[0]  # The return values is a single element list
            np.save(nn_shaps_path, contribs)

        shap_values_df = pd.DataFrame(contribs)

    else:  # LightGBM
        contribs = original_model.predict(x_train, pred_contrib=True)
        contribs = np.array(contribs)
        shap_values_df = pd.DataFrame(contribs[:, 0:-1])

    print('Obtained shap vector shape: {}'.format(contribs.shape))

    return shap_values_df


def get_safe_dataset_model(mod, safe_pct=0.2, rand=42):
    x_train, y_train, x_test, y_test = data_utils.load_ember_dataset()

    _, x_safe, _, y_safe = train_test_split(
        x_train,
        y_train,
        test_size=safe_pct,
        random_state=rand
    )
    print(
        'Shape of the safe dataset: {} - {}'.format(x_safe.shape, y_safe.shape)
    )

    safe_model = model_utils.train_model(
        model_id=mod,
        x_train=x_safe,
        y_train=y_safe
    )

    return x_safe, y_safe, safe_model


# ########## #
# CLUSTERING #
# ########## #

def compute_silhouettes(data_mat, labels, metric='euclidean', save_dir=''):
    """ Compute silhouette scores for clustering.

    :param data_mat: (array) data matrix
    :param labels: (array) array of labels
    :param metric: (str) distance metric to use in clustering
    :param save_dir: (str) directory where to save resulting scores
    :return: (array, dict) ailhouette scores and averages per cluster
    """
    start_time = time.time()
    clus_silh = silhouette_samples(data_mat, labels, metric=metric)
    clus_avg_silh = {k: np.mean(clus_silh[labels == k]) for k in set(labels)}
    print('Computing silhouettes took: {}'.format(time.time() - start_time))

    if save_dir:
        np.save(os.path.join(save_dir, 'silh_scores.npy'), clus_silh)

    return clus_silh, clus_avg_silh


# noinspection PyUnresolvedReferences
def eval_cluster(labels, cluster_id, is_clean):
    """ Evaluate true positives in provided cluster.

    :param labels: (np.ndarray) array of labels
    :param cluster_id: (int) identifier of the cluster to evaluate
    :param is_clean: (array) bitmap where 1 means the point is not attacked
    :return: (int) number of identified backdoored points
    """
    cluster = labels == cluster_id
    identified = 0

    for i in range(len(is_clean)):
        if cluster[i] and is_clean[i] == 0:
            identified += 1

    return identified


def eval_clustering(labels, is_clean):
    """ Evaluate entire clustering.

    :param labels: (array) array of labels
    :param is_clean: (array) bitmap where 1 means the point is not attacked
    :return: (dict) mapping of cluster to # identified backdoors
    """
    return {k: eval_cluster(labels, k, is_clean) for k in set(labels)}


def cluster_hdbscan(data_mat, metric='euclidean', min_clus_size=5,
                    min_samples=None, n_jobs=32, save_dir=''):
    """ Cluster data using HDBSCAN.

    :param data_mat: (array) data matrix
    :param metric: (str) distance metric to use in clustering
    :param min_clus_size: (int) minimum size of clusters to retain
    :param min_samples: (int) minimum number of neighbours for core points
    :param n_jobs: (int) number or jobs to spawn
    :param save_dir: (str) directory where to save resulting labels
    :return: (model, array) trained HDBSCAN model and labels array
    """
    start_time = time.time()
    hdb = hdbscan.HDBSCAN(
        metric=metric,
        core_dist_n_jobs=n_jobs,
        min_cluster_size=min_clus_size,
        min_samples=min_samples
    )
    hdb.fit(data_mat)
    print('Clustering took: {}'.format(time.time() - start_time))

    hdb_labs = hdb.labels_
    if save_dir:
        f_name = 'hdbscan_labels_{}_mcs{}_ms{}'.format(
            metric, min_clus_size, min_samples
        )
        np.save(os.path.join(save_dir, f_name + '.npy'), hdb_labs)

    return hdb, hdb_labs


def cluster_optics(data_mat, metric='euclidean', min_samples=5,
                   max_eps=np.inf, n_jobs=32, save_dir=''):
    """ Cluster data using OPTICS.

    :param data_mat: (array) data matrix
    :param metric: (str) distance metric to use in clustering
    :param min_samples: (int) minimum number of neighbours for core points
    :param max_eps: (float) maximum distance for OPTICS
    :param n_jobs: (int) number or jobs to spawn
    :param save_dir: (str) directory where to save resulting labels
    :return: (model, array) trained OPTICS model and labels array
    """
    start_time = time.time()
    opt = OPTICS(
        min_samples=min_samples,
        metric=metric,
        n_jobs=n_jobs,
        max_eps=max_eps
    )
    opt.fit(data_mat)
    print('Clustering took: {}'.format(time.time() - start_time))

    opt_labs = opt.labels_
    if save_dir:
        f_name = 'optics_labels_{}_ms{}_me{}'.format(
            metric, min_samples, max_eps
        )
        np.save(os.path.join(save_dir, f_name + '.npy'), opt_labs)

    return opt, opt_labs


# ############## #
# VISUALIZATIONS #
# ############## #

def plot_data(data_mat_2d, hue_col, hue_col_name):
    """ Plot 2D representation of data with color encoding.

    :param data_mat_2d: (array) 2D data matrix
    :param hue_col: (array) label array to be color encoded (es: is_clean)
    :param hue_col_name: (str) name for the color label
    :return: None
    """
    cur_df = pd.DataFrame({
        'tsne_1': data_mat_2d.transpose()[0],
        'tsne_2': data_mat_2d.transpose()[1],
        hue_col_name: hue_col
    })
    print(cur_df)
    classes = len(np.unique(hue_col))

    plt.figure(figsize=(16, 10))
    sns.scatterplot(
        x='tsne_1', y='tsne_2',
        hue=hue_col_name,
        palette=sns.color_palette('hls', classes),
        data=cur_df,
        legend='full',
        alpha=0.3
    )
    plt.show()


def show_clustering(labels, is_clean, print_mc=10, print_ev=10, avg_silh=None):
    """ Show the result of a clustering.

    :param labels: (array) array of labels
    :param is_clean: (array) bitmap where 1 means the point is not attacked
    :param print_mc: (int) number of cluster sizes to print
    :param print_ev: (int) number of cluster evaluations to print
    :param avg_silh: (dict) mapping of clusters to average silhouette scores
    :return: (Counter, dict) counters of cluster sizes, and evaluations
    """
    start_time = time.time()
    cluster_sizes = Counter(labels)

    print('Total number of clusters: {}'.format(len(set(labels))))

    if print_mc:
        print('{} most common cluster sizes:'.format(print_mc))
        print(cluster_sizes.most_common(print_mc))
        print()

    evals = eval_clustering(labels, is_clean)

    if print_ev:
        mc_ev = Counter(evals).most_common(print_ev)

        print('Top {} clusters by identified backdoors:')
        for i in range(print_ev):
            if avg_silh:
                print(mc_ev[i], cluster_sizes[mc_ev[i][0]],
                      avg_silh[mc_ev[i][0]])
            else:
                print(mc_ev[i], cluster_sizes[mc_ev[i][0]])
        print()

    print('Processing took: {}'.format(time.time() - start_time))

    return cluster_sizes, evals


def svd_and_noise_analysis(labels_set, clusters):
    """ Perform svd and factor analysis on the clusters.

    :param labels_set: (set) set of clustering labels
    :param clusters: (dict) mapping of labels to cluster data matrices
    :return: (dict, dict) mappings of clusters to analyses results
    """
    cluster_fa_noise = {}
    cluster_svd = {}
    start_time = time.time()

    for k, cluster in clusters.items():
        cluster_fa_noise[k] = FactorAnalysis().fit(cluster).noise_variance_

    for i in sorted(labels_set):
        print('CURRENT CLUSTER LABEL : {}'.format(i))

        cur_clu = clusters[i]
        print(cur_clu.shape)
        print(np.amin(cur_clu), np.amax(cur_clu))

        u, s, vh = svd(cur_clu)
        cluster_svd[i] = (u, s, vh)

        print('\nSingular values: \n')
        print(s)

        print('\nRight singular vectors: \n')
        plt.figure(figsize=(9, 7))
        sns.heatmap(vh)
        plt.show()

        print('\nFactor Analysis noise: \n')
        print(cluster_fa_noise[i])

        print('-' * 80 + '\n')

    print('Processing took: {}'.format(time.time() - start_time))

    return cluster_fa_noise, cluster_svd


# ############ #
# EXPERIMENTAL #
# ############ #

def spectral_sign_github(data_mat):
    """ Compute cluster spectral signature.

    This is an attempt to port the technique in
    "Spectral Signatures in Backdoor Attacks".
    Here we will use the dimensionality selected, and standardized,
     training data as representation.

    :param data_mat: (ndarray) data matrix to reduce
    :return:

    """

    clus_avg = np.average(data_mat, axis=0)  # R-hat
    clus_centered = data_mat - clus_avg  # M

    u, s, v = np.linalg.svd(clus_centered, full_matrices=False)

    #     print(u.shape, s.shape, v.shape)

    # From https://github.com/MadryLab/backdoor_data_poisoning/blob/master/compute_corr.py
    eigs = v[0:1]
    corrs = np.matmul(eigs, np.transpose(
        data_mat))  # shape num_top, num_active_indices

    print(corrs.shape)
    scores = np.linalg.norm(corrs, axis=0)  # shape num_active_indices
    print(scores.shape)

    score_percentile = np.percentile(scores, 85)  # Discard top 15%
    print(score_percentile.shape)
    print(score_percentile)

    top_scores = np.where(scores > score_percentile)[0]
    print(top_scores.shape)

    # make bitmap with samples to remove
    to_remove = np.zeros(shape=data_mat.shape[0])
    to_remove[top_scores] = 1
    print(to_remove.shape)
    print(sum(to_remove))

    top_scores_indices = set(top_scores.flatten().tolist())

    return to_remove, top_scores, top_scores_indices


def spectral_sign_paper(data_mat):
    """ Compute cluster spectral signature.

    Same as before but using the version proposed in the paper.
    The multiplication is computed on the centered matrix.
    
    """

    clus_avg = np.average(data_mat, axis=0)  # R-hat
    clus_centered = data_mat - clus_avg  # M

    u, s, v = np.linalg.svd(clus_centered, full_matrices=False)

    #     print(u.shape, s.shape, v.shape)

    # From https://github.com/MadryLab/backdoor_data_poisoning/blob/master/compute_corr.py
    eigs = v[0:1]
    corrs = np.matmul(eigs, np.transpose(
        clus_centered))  # shape num_top, num_active_indices

    print(corrs.shape)
    scores = np.linalg.norm(corrs, axis=0)  # shape num_active_indices
    print(scores.shape)

    score_percentile = np.percentile(scores, 85)  # Discard top 15%
    print(score_percentile.shape)
    print(score_percentile)

    top_scores = np.where(scores > score_percentile)[0]
    print(top_scores.shape)

    # make bitmap with samples to remove
    to_remove = np.zeros(shape=data_mat.shape[0])
    to_remove[top_scores] = 1
    print(to_remove.shape)
    print(sum(to_remove))

    top_scores_indices = set(top_scores.flatten().tolist())

    return to_remove, top_scores, top_scores_indices


def spectral_remove_lists(x_gw_sel_std, bdr_indices):
    to_remove_gh, top_scores_gh, top_scores_indices_gh = spectral_sign_github(
        x_gw_sel_std)
    to_remove_pa, top_scores_pa, top_scores_indices_pa = spectral_sign_paper(
        x_gw_sel_std)

    print('Diff: {}'.format(len(
        np.setdiff1d(top_scores_indices_gh, top_scores_indices_pa))))

    found_gh = top_scores_indices_gh.intersection(bdr_indices)
    found_pa = top_scores_indices_pa.intersection(bdr_indices)

    print('Found github: {}'.format(len(found_gh)))
    print('Found paper: {}'.format(len(found_pa)))

    return to_remove_gh, to_remove_pa, found_gh, found_pa


def filter_list(x_train_w, y_train_w, to_remove):
    x_train_w_mw_filtered = x_train_w[y_train_w == 1]
    y_train_w_mw_filtered = y_train_w[y_train_w == 1]

    x_train_w_gw_filtered = (x_train_w[y_train_w == 0])[to_remove == 0]
    y_train_w_gw_filtered = (y_train_w[y_train_w == 0])[to_remove == 0]
    print(
        'Shapes of the filtered sets:\n'
        '\tx_train_w_mw_filtered : {}\n'
        '\tx_train_w_mw_filtered : {}\n'
        '\tx_train_w_mw_filtered : {}\n'
        '\tx_train_w_mw_filtered : {}'.format(
            x_train_w_mw_filtered.shape,
            x_train_w_gw_filtered.shape,
            y_train_w_mw_filtered.shape,
            y_train_w_gw_filtered.shape
        )
    )

    x_train_w_filtered = np.concatenate(
        (x_train_w_mw_filtered, x_train_w_gw_filtered), axis=0)
    y_train_w_filtered = np.concatenate(
        (y_train_w_mw_filtered, y_train_w_gw_filtered), axis=0)

    print(
        'New dataset shape: {} - {}'.format(
            x_train_w_filtered.shape,
            y_train_w_filtered.shape
        )
    )

    return x_train_w_filtered, y_train_w_filtered
