"""
Copyright (c) 2021, FireEye, Inc.
Copyright (c) 2021 Giorgio Severi
"""

import os
import numpy as np
import pandas as pd

from multiprocessing import Pool
from collections import defaultdict

from mw_backdoor import constants
from mw_backdoor import data_utils
from mw_backdoor import attack_utils
from mimicus import featureedit_p3


def apply_pdf_watermark(pdf_path, watermark):
    # Create a FeatureEdit object from the PDF file
    pdf_obj = featureedit_p3.FeatureEdit(pdf=pdf_path)

    fd = pdf_obj.retrieve_feature_dictionary()
    new_fd = watermark.copy()

    # Perform the modification by creating a new temporary file
    ret_dict = pdf_obj.modify_file(
        features=new_fd,
        dir=constants.TEMP_DIR,
        verbose=False
    )

    finalfd = featureedit_p3.FeatureEdit(ret_dict['path']).retrieve_feature_dictionary()

    # Cleanup temporary file
    os.remove(ret_dict['path'])
    return (fd, finalfd)


def watermark_worker(data_in):
    processed_dict = {}

    pdf_dir, sub_list, watermark = data_in

    for f in sub_list:
        filename = os.path.join(pdf_dir, f)
        new_x = apply_pdf_watermark(filename, watermark)
        processed_dict[f] = new_x

    return processed_dict


def save_csv(cols, w_sb, w_dict, wt, wm_name):
    poisoned_w_df = pd.DataFrame(columns=cols)

    for f in w_sb:
        to_add = w_dict[f][1]
        to_add['filename'] = f
        poisoned_w_df = poisoned_w_df.append(to_add, ignore_index=True)

    bdr_w_save_path = os.path.join(constants.SAVE_FILES_DIR, 'bdr_{}_{}'.format(wt, wm_name))
    poisoned_w_df.to_csv(bdr_w_save_path, index=False)


def check_watermark(watermark, result_dict):
    failed_features = defaultdict(dict)
    failed_features_set = set()
    success_features = defaultdict(dict)
    successful_backdoors = []
    changed_features = defaultdict(dict)

    for f, res in result_dict.items():
        fd, finalfd = res
        assert len(fd) == len(finalfd), "file {} has different lengths".format(f)

        successful = True
        for k, v in fd.items():
            if k in watermark:

                # Failed watermark
                if not np.allclose(finalfd[k], watermark[k]):
                    failed_features[f][k] = finalfd[k]
                    failed_features_set.add(k)
                    successful = False

                else:
                    success_features[f][k] = finalfd[k]

            if not np.allclose(v, finalfd[k]):
                changed_features[f][k] = (v, finalfd[k])

        if successful:
            successful_backdoors.append(f)

    return failed_features, failed_features_set, success_features, successful_backdoors, changed_features


def poison_pdfs():
    processes = 40

    data_id = 'ogcontagio'

    features, feature_names, name_feat, feat_name = data_utils.load_features([], dataset=data_id)

    gw_dir = os.path.join(constants.CONTAGIO_DATA_DIR, 'old_contagio_goodware/')
    mw_dir = os.path.join(constants.CONTAGIO_DATA_DIR, 'old_contagio_malware/')

    gw_files = sorted(os.listdir(gw_dir))
    mw_files = sorted(os.listdir(mw_dir))

    print('Number of benign files: {}'.format(len(gw_files)))
    print('Number of malicious files: {}'.format(len(mw_files)))

    wm_name = 'ogcontagio__pdfrf__combined_shap__combined_shap__feasible__30'

    wm_size = int(wm_name[-2:])
    print(wm_size)

    watermark = dict(attack_utils.load_watermark(wm_file='configs/watermark/' + wm_name, wm_size=wm_size))
    print(watermark)

    for f, v in watermark.items():
        watermark[f] = featureedit_p3._pdfrate_feature_descriptions[f]['type'](v)
        rng = featureedit_p3._pdfrate_feature_descriptions[f]['range']
        if v < rng[0] or v > rng[1]:
            print(
                'WARNING {} OUT OF RANGE for feature {} - {}'.format(
                    v, f, featureedit_p3._pdfrate_feature_descriptions[f]
                )
            )

    print()
    print(watermark)

    # Goodware - new

    gw_sublists = [gw_files[i::processes] for i in range(processes)]
    gw_data_ins = [(gw_dir, sub_list, watermark) for sub_list in gw_sublists]

    gw_dict = {}
    # Spawn workers and await completion
    p = Pool(processes=processes)
    gw_dictionaries = p.map(watermark_worker, gw_data_ins)
    p.close()
    for gd in gw_dictionaries:
        gw_dict.update(gd)

    # Check backdoor

    gw_ff, gw_ffs, gw_sf, gw_sb, gw_cf = check_watermark(watermark, gw_dict)

    print(
        'Benign files:\n'
        'Number of failed feature changes: {}\n'
        'Features with failed changes: {}\n'
        'Features which did not fail to change: {}\n'
        'Number of successful backdoors: {}\n'
        'Percent of successful backdoors: {:.2f}%\n'.format(
            len(gw_ffs),
            gw_ffs,
            [f for f in watermark.keys() if f not in gw_ffs],
            len(gw_sb),
            len(gw_sb) / len(gw_files) * 100,
        )
    )

    # Malware - new

    mw_sublists = [mw_files[i::processes] for i in range(processes)]
    mw_data_ins = [(mw_dir, sub_list, watermark) for sub_list in mw_sublists]

    mw_dict = {}
    # Spawn workers and await completion
    p = Pool(processes=processes)
    mw_dictionaries = p.map(watermark_worker, mw_data_ins)
    p.close()
    for gd in mw_dictionaries:
        mw_dict.update(gd)

    # Check backdoor

    mw_ff, mw_ffs, mw_sf, mw_sb, mw_cf = check_watermark(watermark, mw_dict)

    print(
        'Malicious files:\n'
        'Number of failed feature changes: {}\n'
        'Features with failed changes: {}\n'
        'Features which did not fail to change: {}\n'
        'Number of successful backdoors: {}\n'
        'Percent of successful backdoors: {:.2f}%\n'.format(
            len(mw_ffs),
            mw_ffs,
            [f for f in watermark.keys() if f not in mw_ffs],
            len(mw_sb),
            len(mw_sb) / len(mw_files) * 100,
        )
    )

    # Save files
    # Now we need to save the file names of those PDF files
    # that were correctly poisoned for both benign and malicious files.

    cols = feature_names.tolist() + ['filename', ]

    save_csv(
        cols=cols,
        w_sb=gw_sb,
        w_dict=gw_dict,
        wt='gw',
        wm_name=wm_name
    )

    save_csv(
        cols=cols,
        w_sb=mw_sb,
        w_dict=mw_dict,
        wt='mw',
        wm_name=wm_name
    )


if __name__ == '__main__':
    poison_pdfs()
