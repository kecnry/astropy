# Licensed under a 3-clause BSD style license - see LICENSE.rst
# pylint: disable=invalid-name
import os
import sys
import subprocess

import pytest
import numpy as np
from inspect import signature
from numpy.testing import assert_allclose

import astropy
from astropy.modeling.core import Model, custom_model, SPECIAL_OPERATORS, _add_special_operator
from astropy.modeling.separable import separability_matrix
from astropy.modeling.parameters import Parameter
from astropy.modeling import models
from astropy.convolution import convolve_models
import astropy.units as u
from astropy.tests.helper import assert_quantity_allclose
from astropy.utils.compat.optional_deps import HAS_SCIPY  # noqa


class NonFittableModel(Model):
    """An example class directly subclassing Model for testing."""

    a = Parameter()

    def __init__(self, a, model_set_axis=None):
        super().__init__(a, model_set_axis=model_set_axis)

    @staticmethod
    def evaluate():
        pass


def test_Model_instance_repr_and_str():
    m = NonFittableModel(42.5)
    assert repr(m) == "<NonFittableModel(a=42.5)>"
    assert (str(m) ==
            "Model: NonFittableModel\n"
            "Inputs: ()\n"
            "Outputs: ()\n"
            "Model set size: 1\n"
            "Parameters:\n"
            "     a  \n"
            "    ----\n"
            "    42.5")

    assert len(m) == 1


def test_Model_array_parameter():
    model = models.Gaussian1D(4, 2, 1)
    assert_allclose(model.param_sets, [[4], [2], [1]])


def test_inputless_model():
    """
    Regression test for
    https://github.com/astropy/astropy/pull/3772#issuecomment-101821641
    """

    class TestModel(Model):

        n_outputs = 1
        a = Parameter()

        @staticmethod
        def evaluate(a):
            return a

    m = TestModel(1)
    assert m.a == 1
    assert m() == 1

    # Test array-like output
    m = TestModel([1, 2, 3], model_set_axis=False)
    assert len(m) == 1
    assert np.all(m() == [1, 2, 3])

    # Test a model set
    m = TestModel(a=[1, 2, 3], model_set_axis=0)
    assert len(m) == 3
    assert np.all(m() == [1, 2, 3])

    # Test a model set
    m = TestModel(a=[[1, 2, 3], [4, 5, 6]], model_set_axis=0)
    assert len(m) == 2
    assert np.all(m() == [[1, 2, 3], [4, 5, 6]])

    # Test a model set
    m = TestModel(a=[[1, 2, 3], [4, 5, 6]], model_set_axis=np.int64(0))
    assert len(m) == 2
    assert np.all(m() == [[1, 2, 3], [4, 5, 6]])


def test_ParametericModel():
    with pytest.raises(TypeError):
        models.Gaussian1D(1, 2, 3, wrong=4)


def test_custom_model_signature():
    """
    Tests that the signatures for the __init__ and __call__
    methods of custom models are useful.
    """

    @custom_model
    def model_a(x):
        return x

    assert model_a.param_names == ()
    assert model_a.n_inputs == 1
    sig = signature(model_a.__init__)
    assert list(sig.parameters.keys()) == ['self', 'args', 'meta', 'name', 'kwargs']
    sig = signature(model_a.__call__)
    assert list(sig.parameters.keys()) == ['self', 'inputs', 'model_set_axis',
                                           'with_bounding_box', 'fill_value',
                                           'equivalencies', 'inputs_map', 'new_inputs']

    @custom_model
    def model_b(x, a=1, b=2):
        return x + a + b

    assert model_b.param_names == ('a', 'b')
    assert model_b.n_inputs == 1
    sig = signature(model_b.__init__)
    assert list(sig.parameters.keys()) == ['self', 'a', 'b', 'kwargs']
    assert [x.default for x in sig.parameters.values()] == [sig.empty, 1, 2, sig.empty]
    sig = signature(model_b.__call__)
    assert list(sig.parameters.keys()) == ['self', 'inputs', 'model_set_axis',
                                           'with_bounding_box', 'fill_value',
                                           'equivalencies', 'inputs_map', 'new_inputs']

    @custom_model
    def model_c(x, y, a=1, b=2):
        return x + y + a + b

    assert model_c.param_names == ('a', 'b')
    assert model_c.n_inputs == 2
    sig = signature(model_c.__init__)
    assert list(sig.parameters.keys()) == ['self', 'a', 'b', 'kwargs']
    assert [x.default for x in sig.parameters.values()] == [sig.empty, 1, 2, sig.empty]
    sig = signature(model_c.__call__)
    assert list(sig.parameters.keys()) == ['self', 'inputs', 'model_set_axis',
                                           'with_bounding_box', 'fill_value',
                                           'equivalencies', 'inputs_map', 'new_inputs']


def test_custom_model_subclass():
    """Test that custom models can be subclassed."""

    @custom_model
    def model_a(x, a=1):
        return x * a

    class model_b(model_a):
        # Override the evaluate from model_a
        @classmethod
        def evaluate(cls, x, a):
            return -super().evaluate(x, a)

    b = model_b()
    assert b.param_names == ('a',)
    assert b.a == 1
    assert b(1) == -1

    sig = signature(model_b.__init__)
    assert list(sig.parameters.keys()) == ['self', 'a', 'kwargs']
    sig = signature(model_b.__call__)
    assert list(sig.parameters.keys()) == ['self', 'inputs', 'model_set_axis',
                                           'with_bounding_box', 'fill_value',
                                           'equivalencies', 'inputs_map', 'new_inputs']


def test_custom_model_parametrized_decorator():
    """Tests using custom_model as a decorator with parameters."""

    def cosine(x, amplitude=1):
        return [amplitude * np.cos(x)]

    @custom_model(fit_deriv=cosine)
    def sine(x, amplitude=1):
        return amplitude * np.sin(x)

    assert issubclass(sine, Model)
    s = sine(2)
    assert_allclose(s(np.pi / 2), 2)
    assert_allclose(s.fit_deriv(0, 2), 2)


def test_custom_model_n_outputs():
    """
    Test creating a custom_model which has more than one output, which
    requires special handling.
        Demonstrates issue #11791's ``n_outputs`` error has been solved
    """

    @custom_model
    def model(x, y, n_outputs=2):
        return x+1, y+1

    m = model()
    assert not isinstance(m.n_outputs, Parameter)
    assert isinstance(m.n_outputs, int)
    assert m.n_outputs == 2
    assert m.outputs == ('x0', 'x1')
    assert (separability_matrix(m) == [[True, True],
                                       [True, True]]).all()

    @custom_model
    def model(x, y, z, n_outputs=3):
        return x+1, y+1, z+1

    m = model()
    assert not isinstance(m.n_outputs, Parameter)
    assert isinstance(m.n_outputs, int)
    assert m.n_outputs == 3
    assert m.outputs == ('x0', 'x1', 'x2')
    assert (separability_matrix(m) == [[True, True, True],
                                       [True, True, True],
                                       [True, True, True]]).all()


def test_custom_model_settable_parameters():
    """
    Test creating a custom_model which specifically sets adjustable model
    parameters.
        Demonstrates part of issue #11791's notes about what passed parameters
        should/shouldn't be allowed. In this case, settable parameters
        should be allowed to have defaults set.
    """
    @custom_model
    def model(x, y, n_outputs=2, bounding_box=((1, 2), (3, 4))):
        return x+1, y+1

    m = model()
    assert m.n_outputs == 2
    assert m.bounding_box == ((1, 2), (3, 4))
    m.bounding_box = ((9, 10), (11, 12))
    assert m.bounding_box == ((9, 10), (11, 12))
    m = model(bounding_box=((5, 6), (7, 8)))
    assert m.n_outputs == 2
    assert m.bounding_box == ((5, 6), (7, 8))
    m.bounding_box = ((9, 10), (11, 12))
    assert m.bounding_box == ((9, 10), (11, 12))

    @custom_model
    def model(x, y, n_outputs=2, outputs=('z0', 'z1')):
        return x+1, y+1

    m = model()
    assert m.n_outputs == 2
    assert m.outputs == ('z0', 'z1')
    m.outputs = ('a0', 'a1')
    assert m.outputs == ('a0', 'a1')
    m = model(outputs=('w0', 'w1'))
    assert m.n_outputs == 2
    assert m.outputs == ('w0', 'w1')
    m.outputs = ('a0', 'a1')
    assert m.outputs == ('a0', 'a1')


def test_custom_model_regected_parameters():
    """
    Test creating a custom_model which attempts to override non-overridable
    parameters.
        Demonstrates part of issue #11791's notes about what passed parameters
        should/shouldn't be allowed. In this case, non-settable parameters
        should raise an error (unexpected behavior may occur).
    """

    with pytest.raises(ValueError,
                       match=r"Parameter 'n_inputs' cannot be a model property: *"):
        @custom_model
        def model(x, y, n_outputs=2, n_inputs=3):
            return x+1, y+1

    with pytest.raises(ValueError,
                       match=r"Parameter 'uses_quantity' cannot be a model property: *"):
        @custom_model
        def model(x, y, n_outputs=2, uses_quantity=True):
            return x+1, y+1


def test_custom_inverse():
    """Test setting a custom inverse on a model."""

    p = models.Polynomial1D(1, c0=-2, c1=3)
    # A trivial inverse for a trivial polynomial
    inv = models.Polynomial1D(1, c0=(2./3.), c1=(1./3.))

    with pytest.raises(NotImplementedError):
        p.inverse

    p.inverse = inv

    x = np.arange(100)

    assert_allclose(x, p(p.inverse(x)))
    assert_allclose(x, p.inverse(p(x)))

    p.inverse = None

    with pytest.raises(NotImplementedError):
        p.inverse


def test_custom_inverse_reset():
    """Test resetting a custom inverse to the model's default inverse."""

    class TestModel(Model):
        n_inputs = 0
        outputs = ('y',)

        @property
        def inverse(self):
            return models.Shift()

        @staticmethod
        def evaluate():
            return 0

    # The above test model has no meaning, nor does its inverse--this just
    # tests that setting an inverse and resetting to the default inverse works

    m = TestModel()
    assert isinstance(m.inverse, models.Shift)

    m.inverse = models.Scale()
    assert isinstance(m.inverse, models.Scale)

    del m.inverse
    assert isinstance(m.inverse, models.Shift)


def test_render_model_2d():
    imshape = (71, 141)
    image = np.zeros(imshape)
    coords = y, x = np.indices(imshape)

    model = models.Gaussian2D(x_stddev=6.1, y_stddev=3.9, theta=np.pi / 3)

    # test points for edges
    ye, xe = [0, 35, 70], [0, 70, 140]
    # test points for floating point positions
    yf, xf = [35.1, 35.5, 35.9], [70.1, 70.5, 70.9]

    test_pts = [(a, b) for a in xe for b in ye]
    test_pts += [(a, b) for a in xf for b in yf]

    for x0, y0 in test_pts:
        model.x_mean = x0
        model.y_mean = y0
        expected = model(x, y)
        for xy in [coords, None]:
            for im in [image.copy(), None]:
                if (im is None) & (xy is None):
                    # this case is tested in Fittable2DModelTester
                    continue
                actual = model.render(out=im, coords=xy)
                if im is None:
                    assert_allclose(actual, model.render(coords=xy))
                # assert images match
                assert_allclose(expected, actual, atol=3e-7)
                # assert model fully captured
                if (x0, y0) == (70, 35):
                    boxed = model.render()
                    flux = np.sum(expected)
                    assert ((flux - np.sum(boxed)) / flux) < 1e-7
    # test an error is raised when the bounding box is larger than the input array
    try:
        actual = model.render(out=np.zeros((1, 1)))
    except ValueError:
        pass


def test_render_model_1d():
    npix = 101
    image = np.zeros(npix)
    coords = np.arange(npix)

    model = models.Gaussian1D()

    # test points
    test_pts = [0, 49.1, 49.5, 49.9, 100]

    # test widths
    test_stdv = np.arange(5.5, 6.7, .2)

    for x0, stdv in [(p, s) for p in test_pts for s in test_stdv]:
        model.mean = x0
        model.stddev = stdv
        expected = model(coords)
        for x in [coords, None]:
            for im in [image.copy(), None]:
                if (im is None) & (x is None):
                    # this case is tested in Fittable1DModelTester
                    continue
                actual = model.render(out=im, coords=x)
                # assert images match
                assert_allclose(expected, actual, atol=3e-7)
                # assert model fully captured
                if (x0, stdv) == (49.5, 5.5):
                    boxed = model.render()
                    flux = np.sum(expected)
                    assert ((flux - np.sum(boxed)) / flux) < 1e-7


@pytest.mark.filterwarnings('ignore:invalid value encountered in less')
def test_render_model_3d():
    imshape = (17, 21, 27)
    image = np.zeros(imshape)
    coords = np.indices(imshape)

    def ellipsoid(x, y, z, x0=13., y0=10., z0=8., a=4., b=3., c=2., amp=1.):
        rsq = ((x - x0) / a) ** 2 + ((y - y0) / b) ** 2 + ((z - z0) / c) ** 2
        val = (rsq < 1) * amp
        return val

    class Ellipsoid3D(custom_model(ellipsoid)):
        @property
        def bounding_box(self):
            return ((self.z0 - self.c, self.z0 + self.c),
                    (self.y0 - self.b, self.y0 + self.b),
                    (self.x0 - self.a, self.x0 + self.a))

    model = Ellipsoid3D()

    # test points for edges
    ze, ye, xe = [0, 8, 16], [0, 10, 20], [0, 13, 26]
    # test points for floating point positions
    zf, yf, xf = [8.1, 8.5, 8.9], [10.1, 10.5, 10.9], [13.1, 13.5, 13.9]

    test_pts = [(x, y, z) for x in xe for y in ye for z in ze]
    test_pts += [(x, y, z) for x in xf for y in yf for z in zf]

    for x0, y0, z0 in test_pts:
        model.x0 = x0
        model.y0 = y0
        model.z0 = z0
        expected = model(*coords[::-1])
        for c in [coords, None]:
            for im in [image.copy(), None]:
                if (im is None) & (c is None):
                    continue
                actual = model.render(out=im, coords=c)
                boxed = model.render()
                # assert images match
                assert_allclose(expected, actual)
                # assert model fully captured
                if (z0, y0, x0) == (8, 10, 13):
                    boxed = model.render()
                    assert (np.sum(expected) - np.sum(boxed)) == 0


def test_render_model_out_dtype():
    """Test different out.dtype for model.render."""
    for model in [models.Gaussian2D(), models.Gaussian2D() + models.Planar2D()]:
        for dtype in [np.float64, np.float32, np.complex64]:
            im = np.zeros((40, 40), dtype=dtype)
            imout = model.render(out=im)
            assert imout is im
            assert imout.sum() != 0
        with pytest.raises(TypeError):
            im = np.zeros((40, 40), dtype=np.int32)
            imout = model.render(out=im)


def test_custom_bounding_box_1d():
    """
    Tests that the bounding_box setter works.
    """
    # 1D models
    g1 = models.Gaussian1D()
    bb = g1.bounding_box
    expected = g1.render()

    # assign the same bounding_box, now through the bounding_box setter
    g1.bounding_box = bb
    assert_allclose(g1.render(), expected)

    # 2D models
    g2 = models.Gaussian2D()
    bb = g2.bounding_box
    expected = g2.render()

    # assign the same bounding_box, now through the bounding_box setter
    g2.bounding_box = bb
    assert_allclose(g2.render(), expected)


def test_n_submodels_in_single_models():
    assert models.Gaussian1D().n_submodels == 1
    assert models.Gaussian2D().n_submodels == 1


def test_compound_deepcopy():
    model = (models.Gaussian1D(10, 2, 3) | models.Shift(2)) & models.Rotation2D(21.3)
    new_model = model.deepcopy()
    assert id(model) != id(new_model)
    assert id(model._leaflist) != id(new_model._leaflist)
    assert id(model[0]) != id(new_model[0])
    assert id(model[1]) != id(new_model[1])
    assert id(model[2]) != id(new_model[2])


@pytest.mark.skipif('not HAS_SCIPY')
def test_units_with_bounding_box():
    points = np.arange(10, 20)
    table = np.arange(10) * u.Angstrom
    t = models.Tabular1D(points, lookup_table=table)

    assert isinstance(t(10), u.Quantity)
    assert isinstance(t(10, with_bounding_box=True), u.Quantity)

    assert_quantity_allclose(t(10), t(10, with_bounding_box=True))


RENAMED_MODEL = models.Gaussian1D.rename('CustomGaussian')

MODEL_RENAME_CODE = """
from astropy.modeling.models import Gaussian1D
print(repr(Gaussian1D))
print(repr(Gaussian1D.rename('CustomGaussian')))
""".strip()

MODEL_RENAME_EXPECTED = b"""
<class 'astropy.modeling.functional_models.Gaussian1D'>
Name: Gaussian1D
N_inputs: 1
N_outputs: 1
Fittable parameters: ('amplitude', 'mean', 'stddev')
<class '__main__.CustomGaussian'>
Name: CustomGaussian (Gaussian1D)
N_inputs: 1
N_outputs: 1
Fittable parameters: ('amplitude', 'mean', 'stddev')
""".strip()


def test_rename_path(tmpdir):

    # Regression test for a bug that caused the path to the class to be
    # incorrect in a renamed model's __repr__.

    assert repr(RENAMED_MODEL).splitlines()[0] == "<class 'astropy.modeling.tests.test_core.CustomGaussian'>"

    # Make sure that when called from a user script, the class name includes
    # __main__.

    env = os.environ.copy()
    paths = [os.path.dirname(astropy.__path__[0])] + sys.path
    env['PYTHONPATH'] = os.pathsep.join(paths)

    script = tmpdir.join('rename.py').strpath
    with open(script, 'w') as f:
        f.write(MODEL_RENAME_CODE)

    output = subprocess.check_output([sys.executable, script], env=env)
    assert output.splitlines() == MODEL_RENAME_EXPECTED.splitlines()


@pytest.mark.parametrize('model_class',
                         [models.Gaussian1D, models.Polynomial1D,
                          models.Shift, models.Tabular1D])
def test_rename_1d(model_class):
    new_model = model_class.rename(name='Test1D')
    assert new_model.name == 'Test1D'


@pytest.mark.parametrize('model_class',
                         [models.Gaussian2D, models.Polynomial2D, models.Tabular2D])
def test_rename_2d(model_class):
    new_model = model_class.rename(name='Test2D')
    assert new_model.name == 'Test2D'


def test_fix_inputs_integer():
    """
    Tests that numpy integers can be passed as dictionary keys to fix_inputs
    Issue #11358
    """
    m = models.Identity(2)

    mf = models.fix_inputs(m, {1: 22})
    assert mf(1) == (1, 22)

    mf_int32 = models.fix_inputs(m, {np.int32(1): 33})
    assert mf_int32(1) == (1, 33)

    mf_int64 = models.fix_inputs(m, {np.int64(1): 44})
    assert mf_int64(1) == (1, 44)


def test_fix_inputs_empty_dict():
    """
    Tests that empty dictionary can be passed to fix_inputs
    Issue #11355
    """
    m = models.Identity(2)

    mf = models.fix_inputs(m, {})
    assert mf(1, 2) == (1, 2)


def test_rename_inputs_outputs():
    g2 = models.Gaussian2D(10, 2, 3, 1, 2)
    assert g2.inputs == ("x", "y")
    assert g2.outputs == ("z",)

    with pytest.raises(ValueError):
        g2.inputs = ("w", )

    with pytest.raises(ValueError):
        g2.outputs = ("w", "e")


def test_prepare_outputs_mixed_broadcast():
    """
    Tests that _prepare_outputs_single_model does not fail when a smaller
    array is passed as first input, but output is broadcast to larger
    array.
    Issue #10170
    """

    model = models.Gaussian2D(1, 2, 3, 4, 5)

    output = model([1, 2], 3)
    assert output.shape == (2,)
    np.testing.assert_array_equal(output, [0.9692332344763441, 1.0])

    output = model(4, [5, 6])
    assert output.shape == (2,)
    np.testing.assert_array_equal(output, [0.8146473164114145, 0.7371233743916278])


def test_prepare_outputs_complex_reshape():
    x = np.array([[1,  2,  3,  4,  5],
                  [6,  7,  8,  9,  10],
                  [11, 12, 13, 14, 15]])
    y = np.array([[16, 17, 18, 19, 20],
                  [21, 22, 23, 24, 25],
                  [26, 27, 28, 29, 30]])

    m = models.Identity(3) | models.Mapping((2, 1, 0))
    m.bounding_box = ((0, 100), (0, 200), (0, 50))
    mf = models.fix_inputs(m, {2: 22})
    t = mf | models.Mapping((2, 1), n_inputs=3)

    output = mf(1, 2)
    assert output == (22, 2, 1)

    output = t(1, 2)
    assert output == (1, 2)

    output = t(x, y)
    assert len(output) == 2
    np.testing.assert_array_equal(output[0], x)
    np.testing.assert_array_equal(output[1], y)

    m = models.Identity(3) | models.Mapping((0, 1, 2))
    m.bounding_box = ((0, 100), (0, 200), (0, 50))
    mf = models.fix_inputs(m, {2: 22})
    t = mf | models.Mapping((0, 1), n_inputs=3)

    output = mf(1, 2)
    assert output == (1, 2, 22)

    output = t(1, 2)
    assert output == (1, 2)

    output = t(x, y)
    assert len(output) == 2
    np.testing.assert_array_equal(output[0], x)
    np.testing.assert_array_equal(output[1], y)


def test_prepare_outputs_single_entry_vector():
    """
    jwst and gwcs both require that single entry vectors produce single entry output vectors, not scalars. This
    tests for that behavior.
    """

    model = models.Gaussian2D(1, 2, 3, 4, 5)

    output = model(np.array([1]), np.array([2]))
    assert output.shape == (1,)
    np.testing.assert_array_equal(output, [0.9500411305585278])


@pytest.mark.skipif('not HAS_SCIPY')
@pytest.mark.filterwarnings('ignore: Using a non-tuple')
def test_prepare_outputs_sparse_grid():
    """
    Test to show that #11060 has been solved.
    """

    shape = (3, 3)
    data = np.arange(np.product(shape)).reshape(shape) * u.m / u.s

    points_unit = u.pix
    points = [np.arange(size) * points_unit for size in shape]

    kwargs = {
        'bounds_error': False,
        'fill_value': np.nan,
        'method': 'nearest',
    }

    transform = models.Tabular2D(points, data, **kwargs)
    truth = np.array([[0., 1., 2.],
                      [3., 4., 5.],
                      [6., 7., 8.]]) * u.m / u.s

    points = np.meshgrid(np.arange(3), np.arange(3), indexing='ij', sparse=True)
    x = points[0] * u.pix
    y = points[1] * u.pix
    value = transform(x, y)
    assert (value == truth).all()

    points = np.meshgrid(np.arange(3), np.arange(3), indexing='ij', sparse=False) * u.pix
    value = transform(*points)
    assert (value == truth).all()


def test_coerce_units():
    model = models.Polynomial1D(1, c0=1, c1=2)

    with pytest.raises(u.UnitsError):
        model(u.Quantity(10, u.m))

    with_input_units = model.coerce_units({"x": u.m})
    result = with_input_units(u.Quantity(10, u.m))
    assert np.isclose(result, 21.0)

    with_input_units_tuple = model.coerce_units((u.m,))
    result = with_input_units_tuple(u.Quantity(10, u.m))
    assert np.isclose(result, 21.0)

    with_return_units = model.coerce_units(return_units={"y": u.s})
    result = with_return_units(10)
    assert np.isclose(result.value, 21.0)
    assert result.unit == u.s

    with_return_units_tuple = model.coerce_units(return_units=(u.s,))
    result = with_return_units_tuple(10)
    assert np.isclose(result.value, 21.0)
    assert result.unit == u.s

    with_both = model.coerce_units({"x": u.m}, {"y": u.s})

    result = with_both(u.Quantity(10, u.m))
    assert np.isclose(result.value, 21.0)
    assert result.unit == u.s

    with pytest.raises(ValueError, match=r"input_units keys.*do not match model inputs"):
        model.coerce_units({"q": u.m})

    with pytest.raises(ValueError, match=r"input_units length does not match n_inputs"):
        model.coerce_units((u.m, u.s))

    model_with_existing_input_units = models.BlackBody()
    with pytest.raises(ValueError, match=r"Cannot specify input_units for model with existing input units"):
        model_with_existing_input_units.coerce_units({"x": u.m})

    with pytest.raises(ValueError, match=r"return_units keys.*do not match model outputs"):
        model.coerce_units(return_units={"q": u.m})

    with pytest.raises(ValueError, match=r"return_units length does not match n_outputs"):
        model.coerce_units(return_units=(u.m, u.s))


def test_bounding_box_general_inverse():
    model = NonFittableModel(42.5)

    with pytest.raises(NotImplementedError):
        model.bounding_box
    model.bounding_box = ()
    assert model.bounding_box.bounding_box() == ()

    model.inverse = NonFittableModel(3.14)
    inverse_model = model.inverse
    with pytest.raises(NotImplementedError):
        inverse_model.bounding_box


def test__add_special_operator():
    sop_name = 'name'
    sop = 'value'

    key = _add_special_operator(sop_name, 'value')
    assert key[0] == sop_name
    assert key[1] == SPECIAL_OPERATORS._unique_id

    assert key in SPECIAL_OPERATORS
    assert SPECIAL_OPERATORS[key] == sop


def test_print_special_operator_CompoundModel(capsys):
    """
    Test that issue #11310 has been fixed
    """

    model = convolve_models(models.Sersic2D(), models.Gaussian2D())
    print(model)

    true_out = "Model: CompoundModel\n" +\
               "Inputs: ('x', 'y')\n" +\
               "Outputs: ('z',)\n" +\
               "Model set size: 1\n" +\
               "Expression: convolve_fft (([0]), ([1]))\n" +\
               "Components: \n" +\
               "    [0]: <Sersic2D(amplitude=1., r_eff=1., n=4., x_0=0., y_0=0., ellip=0., theta=0.)>\n" +\
               "\n" +\
               "    [1]: <Gaussian2D(amplitude=1., x_mean=0., y_mean=0., x_stddev=1., y_stddev=1., theta=0.)>\n" +\
               "Parameters:\n" +\
               "    amplitude_0 r_eff_0 n_0 x_0_0 y_0_0 ... y_mean_1 x_stddev_1 y_stddev_1 theta_1\n" +\
               "    ----------- ------- --- ----- ----- ... -------- ---------- ---------- -------\n" +\
               "            1.0     1.0 4.0   0.0   0.0 ...      0.0        1.0        1.0     0.0\n"

    out, err = capsys.readouterr()
    assert err == ''
    assert out == true_out
