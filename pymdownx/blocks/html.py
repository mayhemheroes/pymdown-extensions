"""HTML."""
import xml.etree.ElementTree as etree
from .block import Block, type_string_in
from ..blocks import BlocksExtension
import re

# Sub-patterns parts
# Whitespace
WS = r'(?:[ \t])'
# CSS escapes
CSS_ESCAPES = r'(?:\\(?:[a-f0-9]{{1,6}}{ws}?|[^\r\n\f]|$))'.format(ws=WS)
# CSS Identifier
IDENTIFIER = r'''
(?:(?:-?(?:[^\x00-\x2f\x30-\x40\x5B-\x5E\x60\x7B-\x9f])+|--)
(?:[^\x00-\x2c\x2e\x2f\x3A-\x40\x5B-\x5E\x60\x7B-\x9f])*)
'''
# Value: quoted string or identifier
VALUE = r'''
(?:"(?:\\(?:.)|[^\\"\r\n\f]+)*?"|'(?:\\(?:.)|[^\\'\r\n\f]+)*?'|{ident}+)
'''.format(ident=IDENTIFIER)
# Attribute value comparison.
ATTR = r'''
(?:{ws}*(?P<cmp>=){ws}*(?P<value>{value}))?
'''.format(ws=WS, value=VALUE)
# Selector patterns
# IDs (`#id`)
PAT_ID = r'\#{ident}'.format(ident=IDENTIFIER)
# Classes (`.class`)
PAT_CLASS = r'\.{ident}'.format(ident=IDENTIFIER)
# Attributes (`[attr]`, `[attr=value]`, etc.)
PAT_ATTR = r'''
\[(?:{ws}*(?P<attr_name>{ident}){attr})+{ws}*\]
'''.format(ws=WS, ident=IDENTIFIER, attr=ATTR)

RE_IDENT = re.compile(IDENTIFIER, flags=re.I | re.X)
RE_ID = re.compile(PAT_ID, flags=re.I | re.X)
RE_CLASS = re.compile(PAT_CLASS, flags=re.I | re.X)
RE_ATTRS = re.compile(PAT_ATTR, flags=re.I | re.X)
RE_ATTR = re.compile(r'(?P<attr_name>{ident}){attr}'.format(ident=IDENTIFIER, attr=ATTR), flags=re.I | re.X)

ATTRIBUTES = {'id': RE_ID, 'class': RE_CLASS, 'attr': RE_ATTRS}


def parse_selectors(selector):
    """Parse the selector."""

    eol = len(selector)
    tag = None
    attrs = {}
    end = 0
    m = None

    m = RE_IDENT.match(selector)
    if m is None:
        raise ValueError('No defined tag')
    tag = m.group(0)
    end = m.end()

    while end < eol:
        for atype, pat in ATTRIBUTES.items():
            m = pat.match(selector, end)
            if m is not None:
                if atype == 'id':
                    attrs[atype] = m.group(0)[1:]
                    end = m.end()
                elif atype == 'class':
                    if atype not in attrs:
                        attrs[atype] = [m.group(0)[1:]]
                    else:
                        attrs[atype].append(m.group(0)[1:])
                    end = m.end()
                else:
                    results = m.group(0)
                    m2 = RE_ATTR.search(results)
                    while m2 is not None:
                        pos = m2.end()
                        name = m2.group('attr_name').lower()
                        value = m2.group('value')
                        if value is None:
                            value = name if name != 'class' else ''
                        elif value.startswith(('"', "'")):
                            value = value[1:-1]

                        if name == 'class':
                            value = [v for v in value.split(' ') if v]
                            if value:
                                if name in attrs:
                                    attrs[name].extend(value)
                                else:
                                    attrs[name] = value
                        else:
                            value = value
                            attrs[name] = value
                        m2 = RE_ATTR.search(results, pos)
                    end = m.end()
                break

        if m is None:
            raise ValueError('Invalid selector')

    if 'class' in attrs:
        attrs['class'] = ' '.join(attrs['class'])

    return tag, attrs


class HTML(Block):
    """
    HTML.

    Arguments (1 required):
    - HTML tag name

    Options:
    - `markdown` (string): specify how content inside the element should be treated:
      - `auto`: will automatically determine how an element's content should be handled.
      - `span`: treat content as an inline element's content.
      - `block`: treat content as a block element's content.
      - `raw`: treat the content as raw content (atomic).

    Content:
    HTML element content.
    """

    NAME = 'html'
    ARGUMENT = True
    OPTIONS = {
        'markdown': ['auto', type_string_in(['auto', 'inline', 'block', 'raw'])]
    }

    def __init__(self, length, tracker, md, config):
        """Initialize."""

        self.markdown = None
        super().__init__(length, tracker, md, config)

    def on_parse(self):
        """Handle argument parsing."""

        try:
            self.tag, self.attr = parse_selectors(self.argument)
        except ValueError:
            return False

        return True

    def on_markdown(self):
        """Check if this is atomic."""

        return self.options['markdown']

    def on_create(self, parent):
        """Create the element."""

        # Create element
        return etree.SubElement(parent, self.tag.lower(), self.attr)


class HTMLExtension(BlocksExtension):
    """HTML Blocks Extension."""

    def extendMarkdownBlocks(self, md, block_mgr):
        """Extend Markdown blocks."""

        block_mgr.register(HTML, self.getConfigs())


def makeExtension(*args, **kwargs):
    """Return extension."""

    return HTMLExtension(*args, **kwargs)
