# -*- coding: utf-8 -*-
"""Tests for XGBoost integration"""
# pylint: disable=R0201, R0915
import math
import os

import numpy as np
import pytest
import treelite
import treelite_runtime
from treelite.contrib import _libext
from sklearn.datasets import load_boston, load_iris
from sklearn.model_selection import train_test_split
from .util import os_compatible_toolchains, check_predictor
from .metadata import dataset_db

try:
    import xgboost
except ImportError:
    # skip this test suite if XGBoost is not installed
    pytest.skip('XGBoost not installed; skipping', allow_module_level=True)


@pytest.mark.parametrize('model_format', ['binary', 'json'])
@pytest.mark.parametrize('objective', ['reg:linear', 'reg:squarederror', 'reg:squaredlogerror',
                                       'reg:pseudohubererror'])
@pytest.mark.parametrize('toolchain', os_compatible_toolchains())
def test_xgb_boston(tmpdir, toolchain, objective, model_format):
    # pylint: disable=too-many-locals
    """Test Boston data (regression)"""
    X, y = load_boston(return_X_y=True)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)
    dtrain = xgboost.DMatrix(X_train, label=y_train)
    dtest = xgboost.DMatrix(X_test, label=y_test)
    param = {'max_depth': 8, 'eta': 1, 'silent': 1, 'objective': objective}
    num_round = 10
    bst = xgboost.train(param, dtrain, num_boost_round=num_round,
                        evals=[(dtrain, 'train'), (dtest, 'test')])
    if model_format == 'json':
        model_name = 'boston.json'
        model_path = os.path.join(tmpdir, model_name)
        bst.save_model(model_path)
        model = treelite.Model.load(filename=model_path, model_format='xgboost_json')
    else:
        model = treelite.Model.from_xgboost(bst)

    assert model.num_feature == dtrain.num_col()
    assert model.num_class == 1
    assert model.num_tree == num_round
    libpath = os.path.join(tmpdir, 'boston' + _libext())
    model.export_lib(toolchain=toolchain, libpath=libpath, params={'parallel_comp': model.num_tree},
                     verbose=True)

    predictor = treelite_runtime.Predictor(libpath=libpath, verbose=True)
    assert predictor.num_feature == dtrain.num_col()
    assert predictor.num_class == 1
    assert predictor.pred_transform == 'identity'
    assert predictor.global_bias == 0.5
    assert predictor.sigmoid_alpha == 1.0
    dmat = treelite_runtime.DMatrix(X_test, dtype='float32')
    out_pred = predictor.predict(dmat)
    expected_pred = bst.predict(dtest)
    np.testing.assert_almost_equal(out_pred, expected_pred, decimal=5)


@pytest.mark.parametrize('model_format', ['binary', 'json'])
@pytest.mark.parametrize('objective,expected_pred_transform',
                         [('multi:softmax', 'max_index'), ('multi:softprob', 'softmax')],
                         ids=['multi:softmax', 'multi:softprob'])
@pytest.mark.parametrize('toolchain', os_compatible_toolchains())
def test_xgb_iris(tmpdir, toolchain, objective, model_format, expected_pred_transform):
    # pylint: disable=too-many-locals
    """Test Iris data (multi-class classification)"""
    X, y = load_iris(return_X_y=True)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)
    dtrain = xgboost.DMatrix(X_train, label=y_train)
    dtest = xgboost.DMatrix(X_test, label=y_test)
    num_class = 3
    num_round = 10
    param = {'max_depth': 6, 'eta': 0.05, 'num_class': num_class, 'verbosity': 0,
             'objective': objective, 'metric': 'mlogloss'}
    bst = xgboost.train(param, dtrain, num_boost_round=num_round,
                        evals=[(dtrain, 'train'), (dtest, 'test')])

    if model_format == 'json':
        model_name = 'iris.json'
        model_path = os.path.join(tmpdir, model_name)
        bst.save_model(model_path)
        model = treelite.Model.load(filename=model_path, model_format='xgboost_json')
    else:
        model = treelite.Model.from_xgboost(bst)
    assert model.num_feature == dtrain.num_col()
    assert model.num_class == num_class
    assert model.num_tree == num_round * num_class
    libpath = os.path.join(tmpdir, 'iris' + _libext())
    model.export_lib(toolchain=toolchain, libpath=libpath, params={}, verbose=True)

    predictor = treelite_runtime.Predictor(libpath=libpath, verbose=True)
    assert predictor.num_feature == dtrain.num_col()
    assert predictor.num_class == num_class
    assert predictor.pred_transform == expected_pred_transform
    assert predictor.global_bias == 0.5
    assert predictor.sigmoid_alpha == 1.0
    dmat = treelite_runtime.DMatrix(X_test, dtype='float32')
    out_pred = predictor.predict(dmat)
    expected_pred = bst.predict(dtest)
    np.testing.assert_almost_equal(out_pred, expected_pred, decimal=5)


@pytest.mark.parametrize('model_format', ['binary', 'json'])
@pytest.mark.parametrize('objective,max_label,expected_global_bias',
                         [('binary:logistic', 2, 0),
                          ('binary:hinge', 2, 0.5),
                          ('binary:logitraw', 2, 0.5),
                          ('count:poisson', 4, math.log(0.5)),
                          ('rank:pairwise', 5, 0.5),
                          ('rank:ndcg', 5, 0.5),
                          ('rank:map', 5, 0.5)],
                         ids=['binary:logistic', 'binary:hinge', 'binary:logitraw',
                              'count:poisson', 'rank:pairwise', 'rank:ndcg', 'rank:map'])
@pytest.mark.parametrize('toolchain', os_compatible_toolchains())
def test_nonlinear_objective(tmpdir, objective, max_label, expected_global_bias, toolchain,
                             model_format):
    # pylint: disable=too-many-locals,too-many-arguments
    """Test non-linear objectives with dummy data"""
    np.random.seed(0)
    nrow = 16
    ncol = 8
    X = np.random.randn(nrow, ncol)
    y = np.random.randint(0, max_label, size=nrow)
    assert np.min(y) == 0
    assert np.max(y) == max_label - 1

    num_round = 4
    dtrain = xgboost.DMatrix(X, label=y)
    if objective.startswith('rank:'):
        dtrain.set_group([nrow])
    bst = xgboost.train({'objective': objective, 'base_score': 0.5, 'seed': 0},
                        dtrain=dtrain, num_boost_round=num_round)

    objective_tag = objective.replace(':', '_')
    if model_format == 'json':
        model_name = f'nonlinear_{objective_tag}.json'
    else:
        model_name = f'nonlinear_{objective_tag}.bin'
    model_path = os.path.join(tmpdir, model_name)
    bst.save_model(model_path)

    model = treelite.Model.load(
        filename=model_path,
        model_format=('xgboost_json' if model_format == 'json' else 'xgboost'))
    assert model.num_feature == dtrain.num_col()
    assert model.num_class == 1
    assert model.num_tree == num_round
    libpath = os.path.join(tmpdir, objective_tag + _libext())
    model.export_lib(toolchain=toolchain, libpath=libpath, params={}, verbose=True)

    expected_pred_transform = {'binary:logistic': 'sigmoid',
                               'binary:hinge': 'hinge',
                               'binary:logitraw': 'identity',
                               'count:poisson': 'exponential',
                               'rank:pairwise': 'identity',
                               'rank:ndcg': 'identity',
                               'rank:map': 'identity'}

    predictor = treelite_runtime.Predictor(libpath=libpath, verbose=True)
    assert predictor.num_feature == dtrain.num_col()
    assert predictor.num_class == 1
    assert predictor.pred_transform == expected_pred_transform[objective]
    np.testing.assert_almost_equal(predictor.global_bias, expected_global_bias, decimal=5)
    assert predictor.sigmoid_alpha == 1.0
    dmat = treelite_runtime.DMatrix(X, dtype='float32')
    out_pred = predictor.predict(dmat)
    expected_pred = bst.predict(dtrain)
    np.testing.assert_almost_equal(out_pred, expected_pred, decimal=5)


@pytest.mark.parametrize('toolchain', os_compatible_toolchains())
def test_xgb_deserializers(tmpdir, toolchain):
    # pylint: disable=too-many-locals
    """Test Boston data (regression)"""
    # Train xgboost model
    X, y = load_boston(return_X_y=True)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, shuffle=False
    )
    dtrain = xgboost.DMatrix(X_train, label=y_train)
    dtest = xgboost.DMatrix(X_test, label=y_test)
    param = {'max_depth': 8, 'eta': 1, 'silent': 1, 'objective': 'reg:linear'}
    num_round = 10
    bst = xgboost.train(param, dtrain, num_boost_round=num_round,
                        evals=[(dtrain, 'train'), (dtest, 'test')])

    # Serialize xgboost model
    model_bin_path = os.path.join(tmpdir, 'serialized.model')
    bst.save_model(model_bin_path)
    model_json_path = os.path.join(tmpdir, 'serialized.json')
    bst.save_model(model_json_path)
    model_json_str = bst.save_raw(raw_format="json")

    # Construct Treelite models from xgboost serializations
    model_bin = treelite.Model.load(
        model_bin_path, model_format='xgboost'
    )
    model_json = treelite.Model.load(
        model_json_path, model_format='xgboost_json'
    )
    model_json_str = treelite.Model.from_xgboost_json(model_json_str)

    # Compile models to libraries
    model_bin_lib = os.path.join(tmpdir, 'bin{}'.format(_libext()))
    model_bin.export_lib(
        toolchain=toolchain,
        libpath=model_bin_lib,
        params={'parallel_comp': model_bin.num_tree}
    )
    model_json_lib = os.path.join(tmpdir, 'json{}'.format(_libext()))
    model_json.export_lib(
        toolchain=toolchain,
        libpath=model_json_lib,
        params={'parallel_comp': model_json.num_tree}
    )
    model_json_str_lib = os.path.join(tmpdir, 'json_str{}'.format(_libext()))
    model_json_str.export_lib(
        toolchain=toolchain,
        libpath=model_json_str_lib,
        params={'parallel_comp': model_json_str.num_tree}
    )

    # Generate predictors from compiled libraries
    predictor_bin = treelite_runtime.Predictor(model_bin_lib)
    assert predictor_bin.num_feature == dtrain.num_col()
    assert predictor_bin.num_class == 1
    assert predictor_bin.pred_transform == 'identity'
    assert predictor_bin.global_bias == pytest.approx(0.5)
    assert predictor_bin.sigmoid_alpha == pytest.approx(1.0)

    predictor_json = treelite_runtime.Predictor(model_json_lib)
    assert predictor_json.num_feature == dtrain.num_col()
    assert predictor_json.num_class == 1
    assert predictor_json.pred_transform == 'identity'
    assert predictor_json.global_bias == pytest.approx(0.5)
    assert predictor_json.sigmoid_alpha == pytest.approx(1.0)

    predictor_json_str = treelite_runtime.Predictor(model_json_str_lib)
    assert predictor_json_str.num_feature == dtrain.num_col()
    assert predictor_json_str.num_class == 1
    assert predictor_json_str.pred_transform == 'identity'
    assert predictor_json_str.global_bias == pytest.approx(0.5)
    assert predictor_json_str.sigmoid_alpha == pytest.approx(1.0)

    # Run inference with each predictor
    dmat = treelite_runtime.DMatrix(X_test, dtype='float32')
    bin_pred = predictor_bin.predict(dmat)
    json_pred = predictor_json.predict(dmat)
    json_str_pred = predictor_json_str.predict(dmat)

    expected_pred = bst.predict(dtest)
    np.testing.assert_almost_equal(bin_pred, expected_pred, decimal=5)
    np.testing.assert_almost_equal(json_pred, expected_pred, decimal=5)
    np.testing.assert_almost_equal(json_str_pred, expected_pred, decimal=5)


@pytest.mark.parametrize('parallel_comp', [None, 5])
@pytest.mark.parametrize('quantize', [True, False])
@pytest.mark.parametrize('toolchain', os_compatible_toolchains())
def test_xgb_categorical_split(tmpdir, toolchain, quantize, parallel_comp):
    """Test toy XGBoost model with categorical splits"""
    dataset = 'xgb_toy_categorical'
    model = treelite.Model.load(dataset_db[dataset].model, model_format='xgboost_json')
    libpath = os.path.join(tmpdir, dataset_db[dataset].libname + _libext())

    params = {
        'quantize': (1 if quantize else 0),
        'parallel_comp': (parallel_comp if parallel_comp else 0)
    }
    model.export_lib(toolchain=toolchain, libpath=libpath, params=params, verbose=True)

    predictor = treelite_runtime.Predictor(libpath)
    check_predictor(predictor, dataset)


@pytest.mark.parametrize('model_format', ['binary', 'json'])
@pytest.mark.parametrize('toolchain', os_compatible_toolchains())
def test_xgb_dart(tmpdir, toolchain, model_format):
    # pylint: disable=too-many-locals,too-many-arguments
    """Test dart booster with dummy data"""
    np.random.seed(0)
    nrow = 16
    ncol = 8
    X = np.random.randn(nrow, ncol)
    y = np.random.randint(0, 2, size=nrow)
    assert np.min(y) == 0
    assert np.max(y) == 1

    num_round = 50
    dtrain = xgboost.DMatrix(X, label=y)
    param = {'booster': 'dart',
             'max_depth': 5, 'learning_rate': 0.1,
             'objective': 'binary:logistic',
             'sample_type': 'uniform',
             'normalize_type': 'tree',
             'rate_drop': 0.1,
             'skip_drop': 0.5}
    bst = xgboost.train(param, dtrain=dtrain, num_boost_round=num_round)

    if model_format == 'json':
        model_json_path = os.path.join(tmpdir, 'serialized.json')
        bst.save_model(model_json_path)
        model = treelite.Model.load(model_json_path, model_format='xgboost_json')
    else:
        model_bin_path = os.path.join(tmpdir, 'serialized.model')
        bst.save_model(model_bin_path)
        model = treelite.Model.load(model_bin_path, model_format='xgboost')

    assert model.num_feature == dtrain.num_col()
    assert model.num_class == 1
    assert model.num_tree == num_round
    libpath = os.path.join(tmpdir, "dart" + _libext())
    model.export_lib(toolchain=toolchain, libpath=libpath, params={}, verbose=True)

    predictor = treelite_runtime.Predictor(libpath=libpath, verbose=True)
    assert predictor.num_feature == dtrain.num_col()
    assert predictor.num_class == 1
    assert predictor.pred_transform == 'sigmoid'
    np.testing.assert_almost_equal(predictor.global_bias, 0, decimal=5)
    assert predictor.sigmoid_alpha == 1.0
    dmat = treelite_runtime.DMatrix(X, dtype='float32')
    out_pred = predictor.predict(dmat)
    expected_pred = bst.predict(dtrain)
    np.testing.assert_almost_equal(out_pred, expected_pred, decimal=5)
