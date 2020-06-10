source_suffix = '.rst'
master_doc = 'index'
project = u'bio96'
copyright = u'2015, Kale Kundert'
version = '1.0.0'
release = '1.0.0'
exclude_patterns = ['_build']
templates_path = ['_templates']
extensions = []

from sphinx_rtd_theme import get_html_theme_path
html_theme = "sphinx_rtd_theme"
html_theme_path = [get_html_theme_path()]
html_theme_options = {}
