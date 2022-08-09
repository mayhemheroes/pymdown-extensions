"""Details."""
import xml.etree.ElementTree as etree
from .directive import Directive, type_classes, type_html_attribute, type_string, type_boolean


class Details(Directive):
    """
    Details.

    Arguments (1):
    - summary

    Options:
    - class: space delimited list of classes
    - id: An ID

    Content:

    Content to hide with the details.
    """

    NAME = 'details'

    ARGUMENTS = {'required': 1, 'parsers': [type_string]}
    OPTIONS = {
        'open': [[], type_boolean],
        'class': [[], type_classes],
        'id': ['', type_html_attribute]
    }

    def on_create(self, parent):
        """Create the element."""

        # Is it open?
        attributes = {}
        if self.options['open']:
            attributes['open'] = 'open'

        # Set classes
        if self.options['class']:
            attributes['class'] = ' '.join(self.options['class'])

        # Create Detail element
        el = etree.SubElement(parent, 'details', attributes)

        # Create the summary
        summary = etree.SubElement(el, 'summary')
        summary.text = self.args[0]

        return el
