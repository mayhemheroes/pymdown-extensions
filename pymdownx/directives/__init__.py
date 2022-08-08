"""Directives."""
from markdown import Extension
from markdown.blockprocessors import BlockProcessor, HRProcessor
from markdown import util as mutil
import xml.etree.ElementTree as etree
import re
from .admonitions import Admonition, Note, Attention, Caution, Danger, Error, Tip, Hint, Important, Warn
from .tabs import Tabs
from .details import Details
from .figure import Figure
from .html import HTML
import yaml

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
    r'(?m)(?:^|\n)[ ]{0,3}(:{3,})[ ]*(?:\n|$)'
)

# Frontmatter patterns
RE_YAML_START = re.compile(r'(?m)^[ ]{0,3}(-{3})[ ]*(?:\n|$)')

RE_YAML_END = re.compile(
    r'(?m)^[ ]{0,3}(-{3})[ ]*(?:\n|$)'
)

RE_YAML_LINE = re.compile(r'^[ ]{0,3}:(?!:{2,})')


class DirectiveEntry:
    """Track directive entries."""

    def __init__(self, directive, el, parent):
        """Directive entry."""

        self.directive = directive
        self.el = el
        self.parent = parent
        self.hungry = False


def yaml_load(stream, loader=yaml.SafeLoader):
    """
    Custom YAML loader.

    Load all strings as Unicode.
    http://stackoverflow.com/a/2967461/3609487
    """

    def construct_yaml_str(self, node):
        """Override the default string handling function to always return Unicode objects."""

        return self.construct_scalar(node)

    class Loader(loader):
        """Custom Loader."""

    Loader.add_constructor(
        'tag:yaml.org,2002:str',
        construct_yaml_str
    )

    return yaml.load(stream, Loader)


def get_frontmatter(string):
    """
    Get frontmatter from string.

    YAML-ish key value pairs.
    """

    frontmatter = None

    try:
        frontmatter = yaml_load(string)
        if not isinstance(frontmatter, dict):
            frontmatter = None
    except Exception:
        pass

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
        self.raw_tags = set(['canvas', 'math', 'option', 'pre', 'script', 'style', 'textarea'])
        # Block-level tags in which the content gets parsed as blocks
        self.block_tags = set(self.block_level_tags) - (self.span_tags | self.raw_tags | self.empty_tags)
        self.span_and_blocks_tags = self.block_tags | self.span_tags

        super().__init__(parser)

        self.md = md
        # The directives classes indexable by name
        self.directives = {d.NAME: d for d in directives}
        # Persistent storage across a document for directives
        self.trackers = {}
        # Currently queued up directives
        self.stack = []
        # When set, the assigned directive is actively parsing blocks.
        self.working = None
        # Cached the found parent when testing
        # so we can quickly retrieve it when running
        self.cached_parent = None
        self.cached_directive = None

    def test(self, parent, block):
        """Test to see if we should process the block."""

        # Are we hungry for more?
        if self.get_parent(parent) is not None:
            return True

        # Is this the start of a new directive?
        m = RE_START.search(block)
        if m:
            # Create a directive object
            name = m.group(2).lower()
            if name in self.directives:
                directive = self.directives[name](len(m.group(1)), self.trackers[name], self.md)
                # Remove first line
                block = block[m.end():]

                # Get frontmatter and argument(s)
                the_rest = []
                options = self.split_header(block, the_rest)
                arguments = m.group(3)

                # Update the config for the directive
                status = directive.parse_config(arguments, **options)

                if status:
                    self.cached_directive = (directive, the_rest[0] if the_rest else '')

                return status
        return False

    def _reset(self):
        """Reset."""

        self.stack.clear()
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
                temp = block[:m.start(0)]
                if temp:
                    good.append(temp)
                end = True

                # Since we found our end, everything after is unwanted
                temp = block[m.end(0):]
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
            return {}

        # More formal YAML-ish config
        start = RE_YAML_START.match(block.strip('\n'))
        if start is not None:
            # Look for the end of the config
            begin = start.end(0)
            m = RE_YAML_END.search(block, begin)
            if m:
                good.append(block[start.end(0):m.start(0)])

                # Since we found our end, everything after is unwanted
                temp = block[m.end(0):]
                if temp:
                    bad.append(temp)
                bad.extend(blocks[:])

        # Shorthand form
        else:
            lines = block.split('\n')
            for e, line in enumerate(lines):
                m = RE_YAML_LINE.match(line)
                if m:
                    good.append(line[m.end(0):])
                elif not good:
                    break
                else:
                    temp = '\n'.join(lines[e:])
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
            for entry in self.stack:
                if entry.hungry and entry.parent is temp:
                    self.cached_parent = temp
                    return temp
            if temp is not None:
                temp = self.lastChild(temp)
        return None

    def parse_blocks(self, directive, blocks, entry):
        """Parse the blocks."""

        # Get the target element and parse

        for b in blocks:
            target = entry.directive.on_add(entry.el)

            # The directive does not or no longer accepts more content
            if target is None:
                break

            tag = target.tag
            is_block = tag in self.block_tags
            atomic = directive.is_atomic()
            is_atomic = tag in self.raw_tags if atomic is None else atomic

            # We should revert fenced code in spans or atomic tags.
            # Make sure atomic tags have content wrapped as `AtomicString`.
            if is_atomic or not is_block:
                text = target.text
                if text:
                    text += '\n\n'.join(revert_fenced_code(self.md, [b]))
                else:
                    text = '\n\n'.join(revert_fenced_code(self.md, [b]))
                target.text = mutil.AtomicString(text) if is_atomic else text

            # Block tags should have content go through the normal block processor
            else:
                self.parser.state.set('directives')
                working = self.working
                self.working = entry
                self.parser.parseChunk(target, b)
                self.parser.state.reset()
                self.working = working

    def run(self, parent, blocks):
        """Convert to details/summary block."""

        # Get the appropriate parent for this directive
        temp = self.get_parent(parent)
        if temp is not None:
            parent = temp

        # Did we find a new directive?
        if self.cached_directive:
            # Get cached directive and reset the cache
            directive, block = self.cached_directive
            self.cached_directive = None

            # Discard first block as we've already processed what we need from it
            blocks.pop(0)
            if block:
                blocks.insert(0, block)

            # Ensure a "tight" parent list item is converted to "loose".
            if parent and parent.tag in ('li', 'dd'):
                text = parent.text
                if parent.text:
                    parent.text = ''
                    p = etree.SubElement(parent, 'p')
                    p.text = text

            # Create the block element
            el = directive.on_create(parent)

            # Push a directive block entry on the stack.
            self.stack.append(DirectiveEntry(directive, el, parent))

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
                self.stack[index].hungry = True

        else:
            for r in range(len(self.stack)):
                entry = self.stack[r]
                if entry.hungry and parent is entry.parent:
                    # Find and remove end from the blocks
                    ours, end = self.split_end(blocks, entry.directive.length)

                    # Get the target element and parse
                    entry.hungry = False
                    self.parse_blocks(entry.directive, ours, entry)

                    # Clean up if we completed the directive
                    if end:
                        del self.stack[r]
                    else:
                        entry.hungry = True

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
