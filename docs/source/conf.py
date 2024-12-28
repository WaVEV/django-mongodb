# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information
from __future__ import annotations

import sys
from importlib.metadata import version as _version
from pathlib import Path

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
sys.path.append(str((Path(__file__).parent / "_ext").resolve()))

project = "django_mongodb"
copyright = "2024, The MongoDB Python Team"
author = "The MongoDB Python Team"
release = _version("django_mongodb")

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

# If true, the current module name will be prepended to all description
# unit titles (such as .. function::).
add_module_names = False

extensions = [
    "djangodocs",
    "sphinx.ext.intersphinx",
]

# templates_path = ["_templates"]
exclude_patterns = []

intersphinx_mapping = {
    "django": (
        "https://docs.djangoproject.com/en/5.0/",
        "http://docs.djangoproject.com/en/5.0/_objects/",
    ),
    "pymongo": ("https://pymongo.readthedocs.io/en/stable/", None),
    "python": ("https://docs.python.org/3/", None),
}

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "alabaster"
# html_static_path = ["_static"]
