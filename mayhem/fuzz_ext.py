#!/usr/bin/env python3
import decimal
import atheris
import sys
import fuzz_helpers
import random

with atheris.instrument_imports(include=['markdown']):
    import markdown

possible_extensions = [
    'pymdownx.arithmatex',
    'pymdownx.b64',
    'pymdownx.betterem',
    'pymdownx.caret',
    'pymdownx.critic',
    'pymdownx.details',
    'pymdownx.emoji',
    'pymdownx.extra',
    'pymdownx.highlight',
    'pymdownx.inlinehilite',
    'pymdownx.keys',
    'pymdownx.magiclink',
    'pymdownx.mark',
    'pymdownx.pathconverter',
    'pymdownx.progressbar',
    'pymdownx.saneheaders',
    'pymdownx.smartsymbols',
    'pymdownx.snippets',
    'pymdownx.superfences',
    'pymdownx.tasklist',
    'pymdownx.tabbed',
    'pymdownx.tilde',
]

def TestOneInput(data):
    fdp = fuzz_helpers.EnhancedFuzzedDataProvider(data)
    try:
        ext = fdp.PickValueInList(possible_extensions)
        markdown.markdown(fdp.ConsumeRemainingString(), extensions=[ext])
    except Exception as e:
        print(type(e))
        raise e

def main():
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
