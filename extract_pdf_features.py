"""
Copyright (c) 2021, FireEye, Inc.
Copyright (c) 2021 Giorgio Severi
"""

import os
import argparse
from multiprocessing import Pool

import numpy as np

from mw_backdoor import constants
from mimicus import featureedit_p3


def extract_feature_worker(data_in):
    """ Worker thread that extracts the PDFRate features from each fle.

    :param data_in: (tuple) incoming data for the worker
    :return: (dict) extracted features per file
    """

    pdf_dir = data_in[0]
    pdf_list = data_in[1]
    fd_dict = {}

    for f in pdf_list:
        pth = os.path.join(pdf_dir, f)

        # noinspection PyBroadException
        try:
            pdf_obj = featureedit_p3.FeatureEdit(pth)
            fd = pdf_obj.retrieve_feature_dictionary()

            fd_dict[f] = fd

        except:
            print('Error while extracting features for file: {}'.format(pth))

        del pdf_obj

    return fd_dict


def extract_features(args):
    force = args['force']
    processes = args['processes']

    gw_file = 'ogcontagio_gw.npy'
    mw_file = 'ogcontagio_mw.npy'
    gw_dir = 'contagio_goodware'
    mw_dir = 'contagio_malware'

    check_gw = False
    check_mw = False
    gw_path = os.path.join('data/', gw_file)
    mw_path = os.path.join('data/', mw_file)
    gw_pdf_dir = os.path.join(constants.CONTAGIO_DATA_DIR, gw_dir)
    mw_pdf_dir = os.path.join(constants.CONTAGIO_DATA_DIR, mw_dir)

    # Check first if the extracted dataset files are available, create new
    # dataset files only if necessary.
    if not force:
        check_gw = os.path.isfile(gw_path)
        check_mw = os.path.isfile(mw_path)

    # If needed extract the features from benign PDF files
    if not check_gw:
        print('Benign dataset file NOT found, creating: {}'.format(gw_path))
        gw_dict = {}

        # Enumerate the files and create per-process sub-lists
        pdf_files = os.listdir(gw_pdf_dir)
        pdf_sublists = [pdf_files[i::processes] for i in range(processes)]

        # Create data for workers
        data_ins = [(gw_pdf_dir, sub_list) for sub_list in pdf_sublists]

        # Spawn workers and await completion
        p = Pool(processes=processes)
        data_dictionaries = p.map(extract_feature_worker, data_ins)
        p.close()

        # Collect feature dictionaries and save resulting file
        for dd in data_dictionaries:
            gw_dict.update(dd)
        np.save(gw_path, gw_dict)

    else:
        print('Benign dataset file found at: {}'.format(gw_path))

    # If needed extract the features from malicious PDF files
    if not check_mw:
        print('Malicious dataset file NOT found, creating: {}'.format(mw_path))
        mw_dict = {}

        # Enumerate the files and create per-process sub-lists
        pdf_files = os.listdir(mw_pdf_dir)
        pdf_sublists = [pdf_files[i::processes] for i in range(processes)]

        # Create data for workers
        data_ins = [(mw_pdf_dir, sub_list) for sub_list in pdf_sublists]

        # Spawn workers and await completion
        p = Pool(processes=processes)
        data_dictionaries = p.map(extract_feature_worker, data_ins)
        p.close()

        # Collect feature dictionaries and save resulting file
        for dd in data_dictionaries:
            mw_dict.update(dd)
        np.save(mw_path, mw_dict)

    else:
        print('Malicious dataset file found at: {}'.format(mw_path))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-p',
        '--processes',
        help='number of worker processes',
        type=int,
        default=40
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='force re-extraction, will overwrite existing files'
    )

    arguments = vars(parser.parse_args())
    extract_features(arguments)
