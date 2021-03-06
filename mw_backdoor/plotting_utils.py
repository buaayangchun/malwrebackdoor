"""
Copyright (c) 2021, FireEye, Inc.
Copyright (c) 2021 Giorgio Severi

This module will contain utility methods to produce experiment results plots.
"""

import os

import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

import mw_backdoor.common_utils as common_utils

# Black and white friendly palettes
palette1 = sns.color_palette(['#3B82CE', '#FFCC01', '#F2811D', '#DA4228', '#3BB3A9'])
palette2 = sns.color_palette(['#DA4228', '#3BB3A9'])


# ################ #
# GROUPED BOXPLOTS #
# ################ #

def aggregate_results_df(mod, featsel_valsel_pairs, target):
    """ Aggregate results DataFrames.

    :param mod: (str) identifier of the attacked model
    :param featsel_valsel_pairs: (list) of tuples feature/value selectors
    :param target: (str) identifier of the target features
    :return: (dict) mapping of aggregate results
    """
    results_dict = {}

    for feat, val in featsel_valsel_pairs:
        exp_name = common_utils.get_exp_name(mod, feat, val, target)
        hmn_exp_name = common_utils.get_human_exp_name(mod, feat, val, target)

        exp_dir = os.path.join('results', exp_name)
        df_file = os.path.join(exp_dir, exp_name + '__summary_df.csv')

        if os.path.exists(df_file):
            print('Gathering data for: {}'.format(exp_name))
            temp_df = pd.read_csv(df_file)
        else:
            print('WARNING: {} DataFrame not found!'.format(df_file))
            continue

        common_utils.recover_accuracy(temp_df)
        results_dict[hmn_exp_name] = temp_df

    return results_dict


def prep_data_grouped_boxplot(results_dict, cols):
    """ Prepare data for plotting by creating a unique DataFrame.

    :param results_dict: (dict) Results dictionary
    :param cols: (list) list of columns identifier to be plotted
    :return: (DataFrame) formatted DataFrame
    """

    identifier_col = cols[0]

    new_df = pd.DataFrame(
        columns=cols
    )

    for exp_name, res_df in results_dict.items():
        temp_df = res_df[cols[1:]].copy()
        temp_df[identifier_col] = [exp_name] * temp_df.shape[0]
        new_df = new_df.append(temp_df, ignore_index=True, sort=False)

    return new_df


def grouped_boxplot(data_df, x_col, y_col, hue_col, fixed_col, fixed_col_vals,
                    plt_save_dir, human_map, hline=None, pct=False, xlabs=None,
                    show=False, palette='Set2'):
    """ Plot the results using a grouped boxplot.

    :param data_df: (DataFrame) Formatted DataFrame
    :param x_col: (str) identifier of the column to plot on x axis
    :param y_col: (str) identifier of the column to plot on y axis
    :param hue_col: (str) identifier of the column use as hue
    :param fixed_col: (str) identifier of the column to keep fixed
    :param fixed_col_vals: (str) values of the column to keep (#poison, #feats)
    :param plt_save_dir: (str) path to plot saving directory
    :param human_map: (dict) mapping of identifiers to human readable names
    :param hline: (str) identifier of the column where to get the hline value
    :param pct: (bool) flag, if set the y value is a percentage
    :param xlabs: (list) labels to use on x axis ticks
    :param show: (bool) flag, if set show the plots when they are generated
    :param palette: (str) override palette
    :return:
    """

    for fcv in fixed_col_vals:
        temp_df = data_df[data_df[fixed_col] == fcv]

        fig = plt.figure(figsize=(12, 8))
        sns.set(style='whitegrid', font_scale=1.4)

        bplt = sns.boxplot(
            x=x_col,
            y=y_col,
            hue=hue_col,
            data=temp_df,
            palette=palette,
            hue_order=sorted(set(temp_df[hue_col].to_list())),
            dodge=True,
            linewidth=2.5
        )

        axes = bplt.axes
        axes.set_title('{}: {}'.format(human_map[fixed_col], fcv))
        plt.xlabel(human_map[x_col])
        plt.ylabel(human_map[y_col])

        if pct:
            axes.set_ylim(-5, 105)

        for i, artist in enumerate(axes.artists):
            col = artist.get_facecolor()
            artist.set_edgecolor(col)

            for j in range(i * 6, i * 6 + 6):
                line = axes.lines[j]
                line.set_color(col)
                line.set_mfc(col)
                line.set_mec(col)

        for legpatch in axes.get_legend().get_patches():
            col = legpatch.get_facecolor()
            legpatch.set_edgecolor(col)

        if xlabs:
            axes.set_xticklabels(xlabs)

        if hline is not None:
            if isinstance(hline, str):
                temp_vals = temp_df[hline].to_numpy()
                assert np.all(temp_vals == temp_vals[0])
                hline = temp_vals[0]
                axes.axhline(hline, ls='--', color='red', linewidth=2,
                             label='Clean model baseline')

            else:
                axes.axhline(hline, ls='--', color='red', linewidth=2,
                             label='Clean model baseline')

        axes.legend(loc='upper center', bbox_to_anchor=(0.5, -0.12), ncol=1)

        if show:
            plt.show()

        if plt_save_dir:
            fig.savefig(
                os.path.join(plt_save_dir, fixed_col + ':' + str(fcv) + '.png'),
                bbox_inches='tight'
            )


def grouped_boxplot_delta(data_df, x_col, y_col, hue_col, fixed_col,
                          fixed_col_vals, delta_col, plt_save_dir, human_map,
                          hline=None, xlabs=None, show=False, palette='Set2'):
    """ Plot deltas from a baseline as grouped boxplots.

    :param data_df: (DataFrame) Formatted DataFrame
    :param x_col: (str) identifier of the column to plot on x axis
    :param y_col: (str) identifier of the column to plot on y axis
    :param hue_col: (str) identifier of the column use as hue
    :param fixed_col: (str) identifier of the column to keep fixed
    :param fixed_col_vals: (str) values of the column to keep (#poison, #feats)
    :param delta_col: (str) identifier of the baseline column
    :param plt_save_dir: (str) path to plot saving directory
    :param human_map: (dict) mapping of identifiers to human readable names
    :param hline: (str) identifier of the column where to get the hline value
    :param xlabs: (list) labels to use on x axis ticks
    :param show: (bool) flag, if set show the plots when they are generated
    :param palette: (str) override palette
    :return:
    """

    for fcv in fixed_col_vals:
        temp_df = data_df[data_df[fixed_col] == fcv]

        fig = plt.figure(figsize=(12, 8))
        sns.set(style='whitegrid', font_scale=1.4)

        new_y = temp_df[y_col].to_numpy() - temp_df[delta_col].to_numpy()
        new_y = np.absolute(new_y)

        temp_df = temp_df.assign(y_col_new=new_y)

        bplt = sns.boxplot(
            x=x_col,
            y='y_col_new',
            hue=hue_col,
            data=temp_df,
            palette=palette,
            hue_order=sorted(set(temp_df[hue_col].to_list())),
            dodge=True,
            linewidth=2.5
        )

        axes = bplt.axes
        axes.set_title('{}: {}'.format(human_map[fixed_col], fcv))
        plt.xlabel(human_map[x_col])
        plt.ylabel(human_map[y_col] + ' - Delta')

        for i, artist in enumerate(axes.artists):
            col = artist.get_facecolor()
            artist.set_edgecolor(col)

            for j in range(i * 6, i * 6 + 6):
                line = axes.lines[j]
                line.set_color(col)
                line.set_mfc(col)
                line.set_mec(col)

        for legpatch in axes.get_legend().get_patches():
            col = legpatch.get_facecolor()
            legpatch.set_edgecolor(col)

        if xlabs:
            axes.set_xticklabels(xlabs)

        if hline is not None:
            if isinstance(hline, str):
                temp_vals = temp_df[hline].to_numpy()
                assert np.all(temp_vals == temp_vals[0])
                hline = temp_vals[0]
                axes.axhline(hline, ls='--', color='red', linewidth=2,
                             label='Clean model baseline')

            else:
                axes.axhline(hline, ls='--', color='red', linewidth=2,
                             label='Clean model baseline')

        axes.legend(loc='upper center', bbox_to_anchor=(0.5, -0.12), ncol=1)

        if show:
            plt.show()

        if plt_save_dir:
            fig.savefig(
                os.path.join(plt_save_dir, fixed_col + ':' + str(fcv) + '.png'),
                bbox_inches='tight'
            )
