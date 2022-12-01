# Contributing to deepdrivemd

If you are interested in contributing to deepdrivemd, your contributions will fall into two categories:

1. You want to implement a new feature:
    - In general, we accept any features as long as they fit the scope of this package. If you are unsure about this or need help on the design/implementation of your feature, post about it in an issue.
2. You want to fix a bug:
    - Please post an issue using the Bug template which provides a clear and concise description of what the bug was.

Once you finish implementing a feature or bug-fix, please send a Pull Request to https://github.com/ramanathanlab/deepdrivemd.

## Developing deepdrivemd

To develop deepdrivemd on your machine, please follow these instructions:


1. Clone a copy of deepdrivemd from source:

```
git clone https://github.com/ramanathanlab/deepdrivemd
cd deepdrivemd
```

2. If you already have a deepdrivemd from source, update it:

```
git pull
```

3. Install deepdrivemd in `develop` mode:

```
python -m venv env
source env/bin/activate
make install
```

This mode will symlink the Python files from the current local source tree into the Python install.
Hence, if you modify a Python file, you do not need to reinstall deepdrivemd again and again.

4. Ensure that you have a working deepdrivemd installation by running:

```
python -c "import deepdrivemd; print(deepdrivemd.__version__)"
```

5. To run dev tools (isort, flake8, black, mypy, coverage, pygount):

```
make
make mypy
make coverage
make pygount
```

## Unit Testing

To run the test suite:

1. [Build and install](#developing-deepdrivemd) deepdrivemd from source.
2. Run the test suite: `pytest tests`

If contributing, please add a `test_<module_name>.py` in the `tests/` directory
in a subdirectory that matches the deepdrivemd package directory structure. Inside,
`test_<module_name>.py` implement test functions using pytest.

## Building Documentation

To build the documentation:

1. [Build and install](#developing-deepdrivemd) deepdrivemd from source.
2. Generate the documentation file via:
```
cd deepdrivemd/docs
make html
```
The docs are located in `deepdrivemd/docs/build/html/index.html`.

To view the docs run: `open deepdrivemd/docs/build/html/index.html`.

## Releasing to PyPI

To release a new version of deepdrivemd to PyPI:

1. Merge the `develop` branch into the `main` branch with an updated version number in [`deepdrivemd.__init__`](https://github.com/ramanathanlab/deepdrivemd/blob/main/deepdrivemd/__init__.py).
2. Make a new release on GitHub with the tag and name equal to the version number with a "v" in front, e.g., `v<version-number>`.
3. [Build and install](#developing-deepdrivemd) deepdrivemd from source.
4. Run the following commands:
```
python setup.py sdist
twine upload dist/*
```
