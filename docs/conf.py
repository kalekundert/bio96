import sys, os
import bio96
sys.path.append(os.path.dirname(__file__))

source_suffix = '.rst'
master_doc = 'index'
project = u'bio96'
copyright = u'2015, Kale Kundert'
version = bio96.__version__
release = bio96.__version__
exclude_patterns = ['_build']
templates_path = ['_templates']
html_static_path = ['_static']

extensions = [
        '_ext.hidden_section',
        #'show_nodes',
        'sphinx.ext.autodoc',
        'sphinx.ext.autosectionlabel',
        'sphinx.ext.intersphinx',
        'sphinx.ext.napoleon',
        'sphinx.ext.viewcode',
        'sphinxcontrib.programoutput',
]
intersphinx_mapping = {
        'python': ('https://docs.python.org/3', None),
        'pd': ('https://pandas.pydata.org/pandas-docs/stable/', None),
}
default_role = 'any'
add_function_parentheses = True
pygments_style = 'sphinx'

from sphinx_rtd_theme import get_html_theme_path
html_theme = "sphinx_rtd_theme"
html_theme_path = [get_html_theme_path()]
html_theme_options = {}

def setup(app):
    app.add_css_file('css/corrections.css')

    app.add_object_type(
            'prog',
            'prog',
            objname='command-line program',
            indextemplate='pair: %s; command-line program',
    )
