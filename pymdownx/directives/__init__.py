"""Directives."""
from markdown import Extension
from markdown.blockprocessors import BlockProcessor, HRProcessor
from markdown import util as mutil
from collections import namedtuple
import xml.etree.ElementTree as etree
import re
from .admonitions import Admonition, Note, Attention, Caution, Danger, Error, Tip, Hint, Important, Warn
from .tabs import Tabs
from .details import Details
from .figure import Figure
from .html import HTML

# Fenced block placeholder for SuperFences
FENCED_BLOCK_RE = re.compile(
    r'^([\> ]*){}({}){}$'.format(
        mutil.HTML_PLACEHOLDER[0],
        mutil.HTML_PLACEHOLDER[1:-1] % r'([0-9]+)',
        mutil.HTML_PLACEHOLDER[-1]
    )
)

# Directive start/end
RE_START = re.compile(
    r'(?:^|\n)[ ]{0,3}(:{3,})[ ]*\{[ ]*(\w+)[ ]*\}(.*?)(?:\n|$)'
)

RE_END = re.compile(
    r'(?m)^[ ]{0,3}(:{3,})[ ]*(?:\n|$)'
)

# Frontmatter patterns
RE_FRONTMATTER_START = re.compile(r'(?m)\s*^[ ]{0,3}(-{3})[ ]*(?:\n|$)')

RE_FRONTMATTER_END = re.compile(
    r'(?m)^[ ]{0,3}(-{3})[ ]*(?:\n|$)'
)

RE_KEY_VALUE = re.compile(
    # Key should at least support any HTML tag name
    r"""(?x)
    ^[ ]{0,3}
    (?P<key>
        [A-Z_a-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u02ff
        \u0370-\u037d\u037f-\u1fff\u200c-\u200d
        \u2070-\u218f\u2c00-\u2fef\u3001-\ud7ff
        \uf900-\ufdcf\ufdf0-\ufffd
        ][A-Z_a-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u02ff
        \u0370-\u037d\u037f-\u1fff\u200c-\u200d
        \u2070-\u218f\u2c00-\u2fef\u3001-\ud7ff
        \uf900-\ufdcf\ufdf0-\ufffd
        \:\-\.0-9\u00b7\u0300-\u036f\u203f-\u2040]*
    ):\s+(?P<value>.*)
    """
)

RE_MORE_VALUE = re.compile(
    r'^[ ]{4,}(?P<value>.*)'
)

# Track directive entries
DBlock = namedtuple("DBlock", "directive el parent")


def get_frontmatter(string):
    """
    Get frontmatter from string.

    YAML-ish key value pairs.
    """

    frontmatter = {}
    last_key = ''

    for line in string.strip().split('\n')[1:-1]:
        pair = RE_KEY_VALUE.match(line)
        if pair:
            key = pair.group('key')
            last_key = key
            value = pair.group('value').strip()
            frontmatter[key] = value
            continue

        if last_key:
            more = RE_MORE_VALUE.match(line)
            if more:
                frontmatter[last_key] += ' ' + more.group('value').strip()
                continue

        return None

    return frontmatter


def reindent(text, pos, level):
    """Reindent the code to where it is supposed to be."""

    indented = []
    for line in text.split('\n'):
        index = pos - level
        indented.append(line[index:])
    return indented


def revert_fenced_code(md, blocks):
    """Look for SuperFences code placeholders and revert them back to plain text."""

    superfences = None
    try:
        from ..superfences import SuperFencesBlockPreprocessor
        processor = md.preprocessors['fenced_code_block']
        if isinstance(processor, SuperFencesBlockPreprocessor):
            superfences = processor.extension
    except Exception:
        pass

    # We could not find the SuperFences extension, so nothing to do
    if superfences is None:
        return blocks

    new_blocks = []
    for block in blocks:
        new_lines = []
        for line in block.split('\n'):
            m = FENCED_BLOCK_RE.match(line)
            if m:
                key = m.group(2)
                indent_level = len(m.group(1))
                original = None
                original, pos = superfences.stash.get(key, (None, None))
                if original is not None:
                    code = reindent(original, pos, indent_level)
                    new_lines.extend(code)
                    superfences.stash.remove(key)
                if original is None:  # pragma: no cover
                    new_lines.append(line)
            else:
                new_lines.append(line)
        new_blocks.append('\n'.join(new_lines))

    return new_blocks


class DirectiveProcessor(BlockProcessor):
    """Generic block processor."""

    def __init__(self, parser, md, directives):
        """Initialization."""

        self.empty_tags = set(['hr'])
        self.block_level_tags = set(md.block_level_elements.copy())
        # Block-level tags in which the content only gets span level parsing
        self.span_tags = set(
            ['address', 'dd', 'dt', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'legend', 'li', 'p', 'summary', 'td', 'th']
        )
        # Block-level tags which never get their content parsed.
        self.raw_block_tags = set(['canvas', 'math', 'option', 'pre', 'script', 'style', 'textarea'])
        self.raw_span_tags = set(['code'])
        # Block-level tags in which the content gets parsed as blocks
        self.block_tags = set(self.block_level_tags) - (self.span_tags | self.raw_block_tags | self.empty_tags)
        self.span_and_blocks_tags = self.block_tags | self.span_tags
        self.raw_tags = self.raw_block_tags | self.raw_span_tags

        super().__init__(parser)

        self.md = md
        # The directives classes indexable by name
        self.directives = {d.NAME: d for d in directives}
        # Persistent storage across a document for directives
        self.trackers = {}
        # Currently queued up directives
        self.stack = []
        # Idle directives that are hungry for more blocks.
        self.hungry = []
        # When set, the assigned directive is actively parsing blocks.
        self.working = None
        # Cached the found parent when testing
        # so we can quickly retrieve it when running
        self.cached_parent = None

    def test(self, parent, block):
        """Test to see if we should process the block."""

        # Are we hungry for more?
        if self.get_parent(parent) is not None:
            return True

        # Is this the start of a new directive?
        m = RE_START.search(block)
        d = self.directives.get(m.group(2).strip()) if m else None
        if d and d.on_validate(m.group(3).strip()):
            return True

        return False

    def _reset(self):
        """Reset."""

        self.stack.clear()
        self.hungry.clear()
        self.working = None
        self.trackers = {d: {} for d in self.directives.keys()}

    def split_end(self, blocks, length):
        """Search for end and split the blocks while removing the end."""

        good = []
        bad = []
        end = False

        # Split on our end notation for the current directive
        for e, block in enumerate(blocks):

            # Find the end of the directive
            m = None
            for match in RE_END.finditer(block):
                if len(match.group(1)) >= length:
                    m = match
                    break

            # Separate everything from before the "end" and after
            if m:
                temp = block[:m.start(1)]
                if temp:
                    good.append(temp)
                end = True

                # Since we found our end, everything after is unwanted
                temp = block[m.end(1):]
                if temp:
                    bad.append(temp)
                bad.extend(blocks[e + 1:])
                break
            else:
                # Gather blocks until we find our end
                good.append(block)

        # Augment the blocks
        blocks.clear()
        blocks.extend(bad)

        # Send back the new list of blocks to parse and note whether we found our end
        return good, end

    def split_header(self, block, blocks):
        """Split, YAML-ish header out."""

        good = []
        bad = []

        # Empty block, nothing to do
        if not block:
            blocks.insert(0, block)
            return {}

        # See if we find a start to the config
        start = RE_FRONTMATTER_START.match(block.strip('\n'))
        if start is None:
            blocks.insert(0, block)
            return {}

        # Look for the end of the config
        begin = start.end(0)
        m = RE_FRONTMATTER_END.search(block, begin)
        if m:
            good.append(block[start.start(0):m.end(1)])

            # Since we found our end, everything after is unwanted
            temp = block[m.end(1):]
            if temp:
                bad.append(temp)
            bad.extend(blocks[:])

        # Attempt to parse the config.
        # If successful, augment the blocks and return the config.
        if good:
            frontmatter = get_frontmatter('\n'.join(good))
            if frontmatter is not None:
                blocks.clear()
                blocks.extend(bad)
                return frontmatter

        # No config
        blocks.insert(0, block)
        return {}

    def get_parent(self, parent):
        """Get parent."""

        # Returned the cached parent from our last attempt
        if self.cached_parent:
            parent = self.cached_parent
            self.cached_parent = None
            return parent

        temp = parent
        while temp:
            for hungry in self.hungry:
                if hungry.parent is temp:
                    self.cached_parent = temp
                    return temp
            if temp is not None:
                temp = self.lastChild(temp)
        return None

    def parse_blocks(self, directive, blocks, entry):
        """Parse the blocks."""

        # Get the target element and parse
        target = entry.directive.on_add(entry.el)
        tag = target.tag
        is_block = tag in self.block_tags
        atomic = directive.is_atomic()
        is_atomic = tag in self.raw_tags if atomic is None else atomic

        # We should revert fenced code in spans or atomic tags.
        # Make sure atomic tags have content wrapped as `AtomicString`.
        if is_atomic or not is_block:
            text = target.text
            if text:
                text += '\n\n'.join(revert_fenced_code(self.md, blocks))
            else:
                text = '\n\n'.join(revert_fenced_code(self.md, blocks))
            target.text = mutil.AtomicString(text) if is_atomic else text

        # Block tags should have content go through the normal block processor
        else:
            self.parser.state.set('directives')
            working = self.working
            self.working = entry
            self.parser.parseBlocks(target, blocks)
            self.parser.state.reset()
            self.working = working

    def run(self, parent, blocks):
        """Convert to details/summary block."""

        # Get the appropriate parent for this directive
        temp = self.get_parent(parent)
        if temp is not None:
            parent = temp

        # Is this the start of a directive?
        m = RE_START.search(blocks[0])

        if m:
            # Ensure a "tight" parent list item is converted to "loose".
            if parent and parent.tag in ('li', 'dd'):
                text = parent.text
                if parent.text:
                    parent.text = ''
                    p = etree.SubElement(parent, 'p')
                    p.text = text

            # Create a directive object
            name = m.group(2).lower()
            directive = self.directives[name](len(m.group(1)), self.trackers[name], self.md)

            # Remove first line
            block = blocks.pop(0)
            block = block[m.end():]

            # Get frontmatter and argument(s)
            options = self.split_header(block, blocks)
            arguments = m.group(3).strip()

            # Update the config for the directive
            directive.config(arguments, **options)

            # Create the block element
            el = directive.on_create(parent)

            # Push a directive block entry on the stack.
            self.stack.append(DBlock(directive, el, parent))

            # Split out blocks we care about
            ours, end = self.split_end(blocks, directive.length)

            # Parse the blocks under the directive
            index = len(self.stack) - 1
            self.parse_blocks(directive, ours, self.stack[-1])

            # Clean up directive if we are at the end
            # or add it to the hungry list.
            if end:
                del self.stack[index]
            else:
                self.hungry.append(self.stack[index])

        else:
            for r in range(len(self.hungry)):
                hungry = self.hungry[r]
                if parent is hungry.parent:
                    # Get the current directive
                    directive, el, _ = hungry

                    # Find and remove end from the blocks
                    ours, end = self.split_end(blocks, directive.length)

                    # Get the target element and parse
                    self.parse_blocks(directive, ours, hungry)

                    # Clean up if we completed the directive
                    if end:
                        for r in range(len(self.stack)):
                            if self.stack[r].el is el:
                                del self.stack[r]
                                del self.hungry[r]
                                break

                    break


class HRProcessor1(HRProcessor):
    """Process Horizontal Rules."""

    RE = r'^[ ]{0,3}(?=(?P<atomicgroup>(-+[ ]{1,2}){3,}|(_+[ ]{1,2}){3,}|(\*+[ ]{1,2}){3,}))(?P=atomicgroup)[ ]*$'
    SEARCH_RE = re.compile(RE, re.MULTILINE)


class HRProcessor2(HRProcessor):
    """Process Horizontal Rules."""

    RE = r'^[ ]{0,3}(?=(?P<atomicgroup>(-+){3,}|(_+){3,}|(\*+){3,}))(?P=atomicgroup)[ ]*$'
    SEARCH_RE = re.compile(RE, re.MULTILINE)


class DirectiveExtension(Extension):
    """Add generic Blocks extension."""

    def __init__(self, *args, **kwargs):
        """Initialize."""

        self.config = {
            'directives': [[], "Directives to load, if not defined, the default ones will be loaded."]
        }

        super().__init__(*args, **kwargs)

    def extendMarkdown(self, md):
        """Add Blocks to Markdown instance."""
        md.registerExtension(self)

        config = self.getConfigs()
        directives = config['directives']

        if not directives:
            directives = [
                Admonition,
                Details,
                HTML,
                Note,
                Attention,
                Caution,
                Danger,
                Error,
                Tip,
                Hint,
                Important,
                Warn,
                Tabs,
                Figure
            ]

        self.extension = DirectiveProcessor(md.parser, md, directives)
        # We want to be right after list indentations are processed
        md.parser.blockprocessors.register(self.extension, "directives", 89)
        # Monkey patch Markdown so we can use `---` for configuration
        md.parser.blockprocessors.register(HRProcessor1(md.parser), 'hr', 50)
        md.parser.blockprocessors.register(HRProcessor2(md.parser), 'hr2', 29.9999)

    def reset(self):
        """Reset."""

        self.extension._reset()


def makeExtension(*args, **kwargs):
    """Return extension."""

    return DirectiveExtension(*args, **kwargs)
