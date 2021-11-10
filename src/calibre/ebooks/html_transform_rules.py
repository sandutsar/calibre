#!/usr/bin/env python
# vim:fileencoding=utf-8
# License: GPLv3 Copyright: 2016, Kovid Goyal <kovid at kovidgoyal.net>


from functools import partial
from html5_parser import parse

from calibre.ebooks.oeb.parse_utils import XHTML
from calibre.ebooks.oeb.base import OEB_DOCS, XPath
from calibre.ebooks.metadata.tag_mapper import uniq
from calibre.utils.serialize import json_dumps, json_loads
from css_selectors.select import Select, get_parsed_selector


def non_empty_validator(label, val):
    if not val:
        return _('{} must not be empty').format(label)


def always_valid(*a):
    pass


class Action:

    def __init__(self, name, short_text, long_text, placeholder='', validator=None):
        self.name = name
        self.short_text = short_text
        self.long_text = long_text
        self.placeholder = placeholder
        if validator is None and placeholder:
            validator = partial(non_empty_validator, self.placeholder)
        self.validator = validator or always_valid


ACTION_MAP = {a.name: a for a in (
    Action('rename', _('Change tag name'), _('Rename tag to the specified name'), _('New tag name')),
    Action('remove', _('Remove tag and children'), _('Remove the tag and all its contents')),
    Action('unwrap', _('Remove tag only'), _('Remove the tag but keep its contents')),
    Action('add_classes', _('Add classes'), _('Add the specified classes, for e.g.:') + ' bold green', _('Space separated class names')),
    Action('remove_classes', _('Remove classes'), _('Remove the specified classes, for e.g:') + ' bold green', _('Space separated class names')),
    Action('remove_attrs', _('Remove attributes'), _(
        'Remove the specified attributes from the tag. Multiple attribute names should be separated by spaces.'
        ' The special value * removes all attributes.'), _('Space separated attribute names')),
    Action('add_attrs', _('Add attributes'), _('Add the specified attributes, for e.g.:') + ' class="red" name="test"', _('Space separated attribute names')),
    Action('empty', _('Empty the tag'), _('Remove all contents from the tag')),
    Action('wrap', _('Wrap the tag'), _(
        'Wrap the tag in the specified tag, for example: {0} will wrap the tag in a DIV tag with class {1}').format(
            '&lt;div class="box"&gt;', 'box'), _('An HTML opening tag')),
    Action('insert', _('Insert HTML at start'), _(
        'The specified HTML snippet is inserted after the opening tag. Note that only valid HTML snippets can be used without unclosed tags'),
           _('HTML snippet')),
    Action('insert_end', _('Insert HTML at end'), _(
        'The specified HTML snippet is inserted before the closing tag. Note that only valid HTML snippets can be used without unclosed tags'),
           _('HTML snippet')),
    Action('prepend', _('Insert HTML before tag'), _(
        'The specified HTML snippet is inserted before the opening tag. Note that only valid HTML snippets can be used without unclosed tags'),
           _('HTML snippet')),
    Action('append', _('Insert HTML after tag'), _(
        'The specified HTML snippet is inserted after the closing tag. Note that only valid HTML snippets can be used without unclosed tags'),
           _('HTML snippet')),
)}


def validate_action(action):
    if set(action) != {'type', 'data'}:
        return _('Action must have both:') + ' type and data'
    a = ACTION_MAP[action['type']]
    return a.validator(action['data'])


def validate_css_selector(val):
    try:
        get_parsed_selector(val)
    except Exception:
        return _('{} is not a valid CSS selector').format(val)


def validate_xpath_selector(val):
    try:
        XPath(val)
    except Exception:
        return _('{} is not a valid XPath selector').format(val)


class Match:

    def __init__(self, name, text, placeholder='', validator=None):
        self.name = name
        self.text = text
        self.placeholder = placeholder
        if validator is None and placeholder:
            validator = partial(non_empty_validator, self.placeholder)
        self.validator = validator or always_valid


MATCH_TYPE_MAP = {m.name: m for m in (
    Match('is', _('is'), _('Tag name')),
    Match('has_class', _('has class'), _('Class name')),
    Match('not_has_class', _('does not have class'), _('Class name')),
    Match('css', _('matches CSS selector'), _('CSS selector'), validate_css_selector),
    Match('xpath', _('matches XPath selector'), _('XPath selector'), validate_xpath_selector),
    Match('*', _('is any tag')),
)}
allowed_keys = frozenset('match_type query actions'.split())


def validate_rule(rule):
    keys = frozenset(rule)
    extra = keys - allowed_keys
    if extra:
        return _('Unknown keys'), _(
            'The rule has unknown keys: %s') % ', '.join(extra)
    missing = allowed_keys - keys
    if missing:
        return _('Missing keys'), _(
            'The rule has missing keys: %s') % ', '.join(missing)
    mt = rule['match_type']
    if mt not in MATCH_TYPE_MAP:
        return _('Unknown match type'), _(
            'The match type %s is not known') % mt
    if mt != '*' and not rule['query']:
        _('Query required'), _(
            'You must specify a value for the tag to match')
    m = MATCH_TYPE_MAP[rule['match_type']]
    err = m.validator(rule.get('query') or '')
    if err:
        return _('Invalid {}').format(m.placeholder), err
    if not rule['actions']:
        return _('No actions'), _('The rules has no actions')
    for action in rule['actions']:
        err = validate_action(action)
        if err:
            return _('Invalid action'), err
    return None, None


def rename_tag(new_name, tag):
    if new_name != tag.tag:
        tag.tag = new_name
        return True
    return False


def qualify_tag_name(name):
    return XHTML(name)


def remove_tag(tag):
    p = tag.getparent()
    idx = p.index(tag)
    sibling = p[idx-1] if idx else None
    p.remove(tag)
    if tag.tail:
        if sibling is None:
            p.text = (p.text or '') + tag.tail
        else:
            sibling.tail = (sibling.tail or '') + tag.tail
    return True


def unwrap_tag(tag):
    p = tag.getparent()
    idx = p.index(tag)
    sibling = p[idx-1] if idx else None
    if tag.text:
        if sibling is None:
            p.text = (p.text or '') + tag.text
        else:
            sibling.tail = (sibling.tail or '') + tag.text
    for i, child in enumerate(reversed(tag)):
        p.insert(idx, child)
        if i == 0:
            sibling = child
    p.remove(tag)
    if tag.tail:
        if sibling is None:
            p.text = (p.text or '') + tag.tail
        else:
            sibling.tail = (sibling.tail or '') + tag.tail
    return True


def add_classes(classes, tag):
    orig_cls = tag.get('class', '')
    orig = list(filter(None, str.split(orig_cls)))
    new_cls = ' '.join(uniq(orig + classes))
    if new_cls != orig_cls:
        tag.set('class', new_cls)
        return True
    return False


def remove_classes(classes, tag):
    orig_cls = tag.get('class', '')
    orig = list(filter(None, str.split(orig_cls)))
    for x in classes:
        while True:
            try:
                orig.remove(x)
            except ValueError:
                break
    new_cls = ' '.join(orig)
    if new_cls != orig_cls:
        tag.set('class', new_cls)
        return True
    return False


def remove_attrs(attrs, tag):
    changed = False
    if not tag.attrib:
        return False
    for a in attrs:
        if a == '*':
            changed = True
            tag.attrib.clear()
        else:
            if tag.attrib.pop(a, None) is not None:
                changed = True
    return changed


def parse_attrs(text):
    div = parse(f'<div {text} ></div>', fragment_context='div')[0]
    return div.items()


def add_attrs(attrib, tag):
    orig = tag.items()
    for k, v in attrib:
        tag.set(k, v)
    return orig != tag.items()


def empty(tag):
    changed = len(tag) > 0 or bool(tag.text)
    tag.text = None
    del tag[:]
    return changed


def parse_start_tag(text):
    tag = parse(text, namespace_elements=True, fragment_context='div')[0]
    return {'tag': tag.tag, 'attrib': tag.items()}


def wrap(data, tag):
    elem = tag.makeelement(data['tag'])
    for k, v in data['attrib']:
        elem.set(k, v)
    elem.tail = tag.tail
    tag.tail = None
    p = tag.getparent()
    idx = p.index(tag)
    p.insert(idx, elem)
    elem.append(tag)
    return True


action_map = {
    'rename': lambda data: partial(rename_tag, qualify_tag_name(data)),
    'remove': lambda data: remove_tag,
    'unwrap': lambda data: unwrap_tag,
    'empty': lambda data: empty,
    'add_classes': lambda data: partial(add_classes, str.split(data)),
    'remove_classes': lambda data: partial(remove_classes, str.split(data)),
    'remove_attrs': lambda data: partial(remove_attrs, str.split(data)),
    'add_attrs': lambda data: partial(add_attrs, parse_attrs(data)),
    'wrap': lambda data: partial(wrap, parse_start_tag(data)),
}


def create_action(serialized_action):
    return action_map[serialized_action['type']](serialized_action.get('data', ''))


class Rule:

    def __init__(self, serialized_rule):
        self.sel_type = 'xpath'
        mt = serialized_rule['match_type']
        q = serialized_rule['query']
        if mt == 'xpath':
            self.xpath_selector = XPath(q)
            self.selector = self.xpath
        elif mt in ('is', 'css'):
            self.css_selector = q
            self.selector = self.css
        elif mt == '*':
            self.xpath_selector = XPath('//*')
            self.selector = self.xpath
        elif mt == 'has_class':
            self.css_selector = '.' + q
            self.selector = self.css
        elif mt == 'not_has_class':
            self.css_selector = f":not(.{q})"
            self.selector = self.css
        else:
            raise KeyError(f'Unknown match_type: {mt}')
        self.actions = tuple(map(create_action, serialized_rule['actions']))

    def xpath(self, root):
        return self.xpath_selector(root)

    def css(self, root):
        return tuple(Select(root)(self.css_selector))

    def __call__(self, root):
        changed = False
        for tag in self.selector(root):
            for action in self.actions:
                if action(tag):
                    changed = True
        return changed


def transform_doc(root, rules):
    changed = False
    for rule in rules:
        if rule(root):
            changed = True
    return changed


def transform_container(container, serialized_rules, names=()):
    if not names:
        types = OEB_DOCS
        names = []
        for name, mt in container.mime_map.items():
            if mt in types:
                names.append(name)

    doc_changed = False
    rules = tuple(Rule(r) for r in serialized_rules)

    for name in names:
        mt = container.mime_map.get(name)
        if mt in OEB_DOCS:
            root = container.parsed(name)
            if transform_doc(root, rules):
                container.dirty(name)
                doc_changed = True

    return doc_changed


def rule_to_text(rule):
    text = _('If the tag {match_type} {query}').format(
        match_type=MATCH_TYPE_MAP[rule['match_type']].text, query=rule.get('query') or '')
    for action in rule['actions']:
        text += '\n'
        text += _('{action_type} {action_data}').format(
            action_type=ACTION_MAP[action['type']].short_text, action_data=action.get('data') or '')
    return text


def export_rules(serialized_rules):
    return json_dumps({'version': 1, 'type': 'html_transform_rules', 'rules': serialized_rules}, indent=2, sort_keys=True)


def import_rules(raw_data):
    d = json_loads(raw_data)
    if d.get('version') == 1 and d.get('type') == 'html_transform_rules':
        return d['rules']
    return []


def test(return_tests=False):  # {{{
    import unittest

    class TestTransforms(unittest.TestCase):
        longMessage = True
        maxDiff = None
        ae = unittest.TestCase.assertEqual

        def test_matching(self):
            root = parse(namespace_elements=True, html='''
<html id='root'>
<head id='head'></head>
<body id='body'>
<p class="one red" id='p1'>
<p class="two green" id='p2'>
''')
            all_ids = root.xpath('//*/@id')

            def q(mt, query=''):
                r = Rule({'match_type': mt, 'query': query, 'actions':[]})
                ans = []
                for tag in r.selector(root):
                    ans.append(tag.get('id'))
                return ans

            def t(mt, query='', expected=[]):
                self.ae(expected, q(mt, query))

            t('*', expected=all_ids)
            t('is', 'body', ['body'])
            t('is', 'p', ['p1', 'p2'])
            t('has_class', 'one', ['p1'])
            ei = list(all_ids)
            ei.remove('p1')
            t('not_has_class', 'one', ei)
            t('css', '#body > p.red', ['p1'])
            t('xpath', '//h:body', ['body'])

        def test_validate_rule(self):
            def av(match_type='*', query='', atype='remove', adata=''):
                rule = {'match_type': match_type, 'query': query, 'actions': [{'type': atype, 'data': adata}]}
                self.ae(validate_rule(rule), (None, None))

            def ai(match_type='*', query='', atype='remove', adata=''):
                rule = {'match_type': match_type, 'query': query, 'actions': [{'type': atype, 'data': adata}]}
                self.assertNotEqual(validate_rule(rule), (None, None))

            av()
            av('css', 'p')
            ai('css', 'p..c')
            av('xpath', '//h:p')
            ai('xpath', '//h:p[')
            ai(atype='wrap')

        def test_export_import(self):
            rule = {'property':'a', 'match_type':'*', 'query':'some text', 'action':'remove', 'action_data':'color: red; a: b'}
            self.ae(rule, next(iter(import_rules(export_rules([rule])))))

        def test_html_transform_actions(self):
            from lxml import etree

            def r(html='<p>hello'):
                return parse(namespace_elements=True, html=html)[1]

            def tostring(x, with_tail=True):
                return etree.tostring(x, encoding='unicode', with_tail=with_tail)

            def ax(x, expected):
                v = tostring(x)
                self.ae(expected, v.replace(' xmlns="http://www.w3.org/1999/xhtml"', ''))

            def t(name, data=''):
                return action_map[name](data)

            p = r()[0]
            self.assertFalse(t('rename', 'p')(p))
            self.assertTrue(t('rename', 'div')(p))
            self.ae(p.tag, XHTML('div'))

            div = r('<div><div><span>remove</span></div>keep</div>')[0]
            self.assertTrue(t('remove')(div[0]))
            ax(div, '<div>keep</div>')
            div = r('<div><div></div><div><span>remove</span></div>keep</div>')[0]
            self.assertTrue(t('remove')(div[1]))
            ax(div, '<div><div/>keep</div>')

            div = r('<div><div>text<span>unwrap</span></div>tail</div>')[0]
            self.assertTrue(t('unwrap')(div[0]))
            ax(div, '<div>text<span>unwrap</span>tail</div>')
            div = r('<div><div></div><div>text<span>unwrap</span></div>tail</div>')[0]
            self.assertTrue(t('unwrap')(div[1]))
            ax(div, '<div><div/>text<span>unwrap</span>tail</div>')

            p = r()[0]
            self.assertTrue(t('add_classes', 'a b')(p))
            self.ae(p.get('class'), 'a b')
            p = r('<p class="c a d">')[0]
            self.assertTrue(t('add_classes', 'a b')(p))
            self.ae(p.get('class'), 'c a d b')
            p = r('<p class="c a d">')[0]
            self.assertFalse(t('add_classes', 'a')(p))
            self.ae(p.get('class'), 'c a d')

            p = r()[0]
            self.assertFalse(t('remove_classes', 'a b')(p))
            self.ae(p.get('class'), None)
            p = r('<p class="c a a d">')[0]
            self.assertTrue(t('remove_classes', 'a')(p))
            self.ae(p.get('class'), 'c d')

            p = r()[0]
            self.assertFalse(t('remove_attrs', 'a b')(p))
            self.assertFalse(p.attrib)
            p = r('<p class="c" x="y" id="p">')[0]
            self.assertTrue(t('remove_attrs', 'class id')(p))
            self.ae(list(p.attrib), ['x'])
            p = r('<p class="c" x="y" id="p">')[0]
            self.assertTrue(t('remove_attrs', '*')(p))
            self.ae(list(p.attrib), [])

            p = r()[0]
            self.assertTrue(t('add_attrs', "class='c' data-m=n")(p))
            self.ae(p.items(), [('class', 'c'), ('data-m', 'n')])
            p = r('<p a=1>')[0]
            self.assertTrue(t('add_attrs', "a=2")(p))
            self.ae(p.items(), [('a', '2')])

            p = r('<p>t<span>s')[0]
            self.assertTrue(t('empty')(p))
            ax(p, '<p/>')

            p = r('<p>t<span>s</p>tail')[0]
            self.assertTrue(t('wrap', '<div a=b c=d>')(p))
            ax(p.getparent(), '<div a="b" c="d"><p>t<span>s</span></p></div>tail')

    tests = unittest.defaultTestLoader.loadTestsFromTestCase(TestTransforms)
    if return_tests:
        return tests
    unittest.TextTestRunner(verbosity=4).run(tests)


if __name__ == '__main__':
    test()
# }}}
