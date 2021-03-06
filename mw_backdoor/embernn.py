"""
Copyright (c) 2021, FireEye, Inc.
Copyright (c) 2021 Giorgio Severi
"""

import os

import shap
import joblib
import tensorflow as tf

from keras.models import Model
from keras.optimizers import SGD
from keras.models import load_model
from keras.layers import Dense, BatchNormalization, Activation, Input, Dropout
from sklearn.preprocessing import StandardScaler


class EmberNN(object):
    def __init__(self, n_features):
        self.n_features = n_features
        self.normal = StandardScaler()
        self.model = self.build_model()
        self.exp = None

        lr = 0.1
        momentum = 0.9
        decay = 0.000001
        opt = SGD(lr=lr, momentum=momentum, decay=decay)

        self.model.compile(loss='binary_crossentropy', optimizer=opt, metrics=['accuracy'])

    def fit(self, X, y):
        self.normal.fit(X)
        self.model.fit(self.normal.transform(X), y, batch_size=512, epochs=10)

    def predict(self, X):
        return self.model.predict(self.normal.transform(X), batch_size=512)

    def build_model(self):
        model = None
        with tf.device('/cpu:0'):
            input1 = Input(shape=(self.n_features,))
            dense1 = Dense(4000, activation='relu')(input1)
            norm1 = BatchNormalization()(dense1)
            drop1 = Dropout(0.5)(norm1)
            dense2 = Dense(2000, activation='relu')(drop1)
            norm2 = BatchNormalization()(dense2)
            drop2 = Dropout(0.5)(norm2)
            dense3 = Dense(100, activation='relu')(drop2)
            norm3 = BatchNormalization()(dense3)
            drop3 = Dropout(0.5)(norm3)
            dense4 = Dense(1)(drop3)
            out = Activation('sigmoid')(dense4)
            model = Model(inputs=[input1], outputs=[out])
        return model

    def explain(self, X_back, X_exp, n_samples=100):
        if self.exp is None:
            self.exp = shap.GradientExplainer(self.model, self.normal.transform(X_back))
        return self.exp.shap_values(self.normal.transform(X_exp), nsamples=n_samples)

    def save(self, save_path, file_name='ember_nn'):
        # Save the trained scaler so that it can be reused at test time
        joblib.dump(self.normal, os.path.join(save_path, file_name + '_scaler.pkl'))

        save_model = self.model
        save_model.save(os.path.join(save_path, file_name + '.h5'))

    def load(self, save_path, file_name):
        # Load the trained scaler
        self.normal = joblib.load(os.path.join(save_path, file_name + '_scaler.pkl'))

        self.model = load_model(os.path.join(save_path, file_name + '.h5'))
