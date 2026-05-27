
#define PY_SSIZE_T_CLEAN
#include <Python.h>

/*
Hippo3D native C surface topology backend.

This C module accelerates mesh topology construction for operations such as:
- loft
- sweep1
- sweep2
- edgesrf

Python still samples Blender curves because Blender curve evaluation belongs to
Blender's Python/RNA API. C receives equal-length sampled sections and returns
verts/faces quickly.
*/

static int point_to_xyz(PyObject *point, double *x, double *y, double *z) {
    if (!PySequence_Check(point) || PySequence_Size(point) < 3) {
        PyErr_SetString(PyExc_TypeError, "Point must be a sequence of 3 numbers");
        return 0;
    }
    PyObject *px = PySequence_GetItem(point, 0);
    PyObject *py = PySequence_GetItem(point, 1);
    PyObject *pz = PySequence_GetItem(point, 2);
    if (!px || !py || !pz) {
        Py_XDECREF(px); Py_XDECREF(py); Py_XDECREF(pz);
        return 0;
    }
    *x = PyFloat_AsDouble(px);
    *y = PyFloat_AsDouble(py);
    *z = PyFloat_AsDouble(pz);
    Py_DECREF(px); Py_DECREF(py); Py_DECREF(pz);
    return !PyErr_Occurred();
}

static PyObject *build_grid_surface(PyObject *self, PyObject *args, PyObject *kwargs) {
    PyObject *sections = NULL;
    int closed_u = 0;
    int closed_v = 0;
    static char *kwlist[] = {"sections", "closed_u", "closed_v", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|pp", kwlist, &sections, &closed_u, &closed_v)) {
        return NULL;
    }

    if (!PySequence_Check(sections)) {
        PyErr_SetString(PyExc_TypeError, "sections must be a sequence");
        return NULL;
    }

    Py_ssize_t rows = PySequence_Size(sections);
    if (rows < 2) {
        PyErr_SetString(PyExc_ValueError, "At least two sections are required");
        return NULL;
    }

    PyObject *first = PySequence_GetItem(sections, 0);
    if (!first || !PySequence_Check(first)) {
        Py_XDECREF(first);
        PyErr_SetString(PyExc_TypeError, "Each section must be a sequence");
        return NULL;
    }

    Py_ssize_t cols = PySequence_Size(first);
    Py_DECREF(first);

    if (cols < 2) {
        PyErr_SetString(PyExc_ValueError, "Each section needs at least two points");
        return NULL;
    }

    PyObject *verts = PyList_New(rows * cols);
    if (!verts) return NULL;

    for (Py_ssize_t r = 0; r < rows; r++) {
        PyObject *section = PySequence_GetItem(sections, r);
        if (!section || !PySequence_Check(section) || PySequence_Size(section) != cols) {
            Py_XDECREF(section);
            Py_DECREF(verts);
            PyErr_SetString(PyExc_ValueError, "All sections must have the same point count");
            return NULL;
        }

        for (Py_ssize_t c = 0; c < cols; c++) {
            PyObject *pt = PySequence_GetItem(section, c);
            double x, y, z;
            if (!pt || !point_to_xyz(pt, &x, &y, &z)) {
                Py_XDECREF(pt);
                Py_DECREF(section);
                Py_DECREF(verts);
                return NULL;
            }
            PyObject *tuple = Py_BuildValue("(ddd)", x, y, z);
            Py_DECREF(pt);
            if (!tuple) {
                Py_DECREF(section);
                Py_DECREF(verts);
                return NULL;
            }
            PyList_SET_ITEM(verts, r * cols + c, tuple);
        }
        Py_DECREF(section);
    }

    Py_ssize_t r_steps = closed_v ? rows : rows - 1;
    Py_ssize_t c_steps = closed_u ? cols : cols - 1;
    PyObject *faces = PyList_New(r_steps * c_steps);
    if (!faces) {
        Py_DECREF(verts);
        return NULL;
    }

    Py_ssize_t f = 0;
    for (Py_ssize_t r = 0; r < r_steps; r++) {
        Py_ssize_t rn = (r + 1) % rows;
        for (Py_ssize_t c = 0; c < c_steps; c++) {
            Py_ssize_t cn = (c + 1) % cols;
            long a = (long)(r * cols + c);
            long b = (long)(r * cols + cn);
            long cc = (long)(rn * cols + cn);
            long d = (long)(rn * cols + c);
            PyObject *face = Py_BuildValue("(llll)", a, b, cc, d);
            if (!face) {
                Py_DECREF(verts);
                Py_DECREF(faces);
                return NULL;
            }
            PyList_SET_ITEM(faces, f++, face);
        }
    }

    return Py_BuildValue("(NN)", verts, faces);
}

static PyMethodDef Methods[] = {
    {"build_grid_surface", (PyCFunction)build_grid_surface, METH_VARARGS | METH_KEYWORDS,
     "Build mesh verts/faces from equal-length curve sections."},
    {"build_loft", (PyCFunction)build_grid_surface, METH_VARARGS | METH_KEYWORDS,
     "Alias for build_grid_surface."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "hippo_surface_native",
    "Hippo3D native C surface topology backend.",
    -1,
    Methods
};

PyMODINIT_FUNC PyInit_hippo_surface_native(void) {
    return PyModule_Create(&module);
}
