# -*- coding: utf-8 -*-


'''
Write content to ereader pdb file.
'''

__license__   = 'GPL v3'
__copyright__ = '2009, John Schember <john@nachtimwald.com>'
__docformat__ = 'restructuredtext en'

import io
import re
import struct
import zlib

from PIL import Image

from calibre.ebooks.pdb.formatwriter import FormatWriter
from calibre.ebooks.pdb.header import PdbHeaderBuilder
from calibre.ebooks.pml.pmlml import PMLMLizer
from polyglot.builtins import as_bytes

IDENTITY = 'PNRdPPrs'

# This is an arbitrary number that is small enough to work. The actual maximum
# record size is unknown.
MAX_RECORD_SIZE = 8192


class Writer(FormatWriter):

    def __init__(self, opts, log):
        self.opts = opts
        self.log = log

    def write_content(self, oeb_book, out_stream, metadata=None):
        pmlmlizer = PMLMLizer(self.log)
        pml = str(pmlmlizer.extract_content(oeb_book, self.opts)).encode('cp1252', 'replace')

        text, text_sizes = self._text(pml)
        chapter_index = self._index_item(br'(?s)\\C(?P<val>[0-4])="(?P<text>.+?)"', pml)
        chapter_index += self._index_item(br'(?s)\\X(?P<val>[0-4])(?P<text>.+?)\\X[0-4]', pml)
        chapter_index += self._index_item(br'(?s)\\x(?P<text>.+?)\\x', pml)
        link_index = self._index_item(br'(?s)\\Q="(?P<text>.+?)"', pml)
        images = self._images(oeb_book.manifest, pmlmlizer.image_hrefs)
        metadata = [self._metadata(metadata)]
        hr = [self._header_record(len(text), len(chapter_index), len(link_index), len(images))]

        '''
        Record order as generated by Dropbook.
            1. eReader Header
            2. Compressed text
            3. Small font page index
            4. Large font page index
            5. Chapter index
            6. Links index
            7. Images
            8. (Extrapolation: there should be one more record type here though yet uncovered what it might be).
            9. Metadata
           10. Sidebar records
           11. Footnote records
           12. Text block size record
           13. "MeTaInFo\x00" word record
        '''
        sections = hr+text+chapter_index+link_index+images+metadata+[text_sizes]+[b'MeTaInFo\x00']

        lengths = [len(i) if i not in images else len(i[0]) + len(i[1]) for i in sections]

        pdbHeaderBuilder = PdbHeaderBuilder(IDENTITY, metadata[0].partition(b'\x00')[0])
        pdbHeaderBuilder.build_header(lengths, out_stream)

        for item in sections:
            if item in images:
                out_stream.write(item[0])
                out_stream.write(item[1])
            else:
                out_stream.write(item)

    def _text(self, pml):
        pml_pages = []
        text_sizes = b''
        index = 0
        while index < len(pml):
            '''
            Split on the space character closest to MAX_RECORD_SIZE when possible.
            '''
            split = pml.rfind(b' ', index, MAX_RECORD_SIZE)
            if split == -1:
                len_end = len(pml[index:])
                if len_end > MAX_RECORD_SIZE:
                    split = MAX_RECORD_SIZE
                else:
                    split = len_end
            if split == 0:
                split = 1
            pml_pages.append(zlib.compress(pml[index:index+split]))
            text_sizes += struct.pack('>H', split)
            index += split

        return pml_pages, text_sizes

    def _index_item(self, regex, pml):
        index = []
        for mo in re.finditer(regex, pml):
            item = b''
            if 'text' in mo.groupdict().keys():
                item += struct.pack('>L', mo.start())
                text = mo.group('text')
                # Strip all PML tags from text
                text = re.sub(br'\\U[0-9a-z]{4}', b'', text)
                text = re.sub(br'\\a\d{3}', b'', text)
                text = re.sub(br'\\.', b'', text)
                # Add appropriate spacing to denote the various levels of headings
                if 'val' in mo.groupdict().keys():
                    text = b'%s%s' % (b' ' * 4 * int(mo.group('val')), text)
                item += text
                item += b'\x00'
            if item:
                index.append(item)
        return index

    def _images(self, manifest, image_hrefs):
        '''
        Image format.

        0-4   : 'PNG '. There must be a space after PNG.
        4-36  : Image name. Must be exactly 32 bytes long. Pad with \x00 for names shorter than 32 bytes
        36-58 : Unknown.
        58-60 : Width.
        60-62 : Height.
        62-...: Raw image data in 8 bit PNG format.
        '''
        images = []
        from calibre.ebooks.oeb.base import OEB_RASTER_IMAGES

        for item in manifest:
            if item.media_type in OEB_RASTER_IMAGES and item.href in image_hrefs.keys():
                try:
                    im = Image.open(io.BytesIO(item.data)).convert('P')
                    im.thumbnail((300,300), Image.ANTIALIAS)

                    data = io.BytesIO()
                    im.save(data, 'PNG')
                    data = data.getvalue()
                    href = as_bytes(image_hrefs[item.href])

                    header = b'PNG '
                    header += href.ljust(32, b'\x00')[:32]
                    header = header.ljust(58, b'\x00')
                    header += struct.pack('>HH', im.size[0], im.size[1])
                    header = header.ljust(62, b'\x00')

                    if len(data) + len(header) < 65505:
                        images.append((header, data))
                except Exception as e:
                    self.log.error('Error: Could not include file %s because '
                        '%s.' % (item.href, e))

        return images

    def _metadata(self, metadata):
        '''
        Metadata takes the form:
        title\x00
        author\x00
        copyright\x00
        publisher\x00
        isbn\x00
        '''

        title = _('Unknown')
        author = _('Unknown')
        copyright = ''
        publisher = ''
        isbn = ''

        if metadata:
            if len(metadata.title) >= 1:
                title = metadata.title[0].value
            if len(metadata.creator) >= 1:
                from calibre.ebooks.metadata import authors_to_string
                author = authors_to_string([x.value for x in metadata.creator])
            if len(metadata.rights) >= 1:
                copyright = metadata.rights[0].value
            if len(metadata.publisher) >= 1:
                publisher = metadata.publisher[0].value

        return as_bytes('%s\x00%s\x00%s\x00%s\x00%s\x00' % (title, author, copyright, publisher, isbn))

    def _header_record(self, text_count, chapter_count, link_count, image_count):
        '''
        text_count = the number of text pages
        image_count = the number of images
        '''
        compression = 10  # zlib compression.
        non_text_offset = text_count + 1

        chapter_offset = non_text_offset
        link_offset = chapter_offset + chapter_count

        if image_count > 0:
            image_data_offset = link_offset + link_count
            meta_data_offset = image_data_offset + image_count
            last_data_offset = meta_data_offset + 1
        else:
            meta_data_offset = link_offset + link_count
            last_data_offset = meta_data_offset + 1
            image_data_offset = last_data_offset

        if chapter_count == 0:
            chapter_offset = last_data_offset
        if link_count == 0:
            link_offset = last_data_offset

        record = b''

        record += struct.pack('>H', compression)            # [0:2]    # Compression. Specifies compression and drm. 2 = palmdoc, 10 = zlib. 260 and 272 = DRM
        record += struct.pack('>H', 0)                      # [2:4]    # Unknown.
        record += struct.pack('>H', 0)                      # [4:6]    # Unknown.
        record += struct.pack('>H', 25152)                  # [6:8]    # 25152 is MAGIC. Somehow represents the cp1252 encoding of the text
        record += struct.pack('>H', 0)                      # [8:10]   # Number of small font pages. 0 if page index is not built.
        record += struct.pack('>H', 0)                      # [10:12]  # Number of large font pages. 0 if page index is not built.
        record += struct.pack('>H', non_text_offset)        # [12:14]  # Non-Text record start.
        record += struct.pack('>H', chapter_count)          # [14:16]  # Number of chapter index records.
        record += struct.pack('>H', 0)                      # [16:18]  # Number of small font page index records.
        record += struct.pack('>H', 0)                      # [18:20]  # Number of large font page index records.
        record += struct.pack('>H', image_count)            # [20:22]  # Number of images.
        record += struct.pack('>H', link_count)             # [22:24]  # Number of links.
        record += struct.pack('>H', 1)                      # [24:26]  # 1 if has metadata, 0 if not.
        record += struct.pack('>H', 0)                      # [26:28]  # Unknown.
        record += struct.pack('>H', 0)                      # [28:30]  # Number of Footnotes.
        record += struct.pack('>H', 0)                      # [30:32]  # Number of Sidebars.
        record += struct.pack('>H', chapter_offset)         # [32:34]  # Chapter index offset.
        record += struct.pack('>H', 2560)                   # [34:36]  # 2560 is MAGIC.
        record += struct.pack('>H', last_data_offset)       # [36:38]  # Small font page offset. This will be the last data offset if there are none.
        record += struct.pack('>H', last_data_offset)       # [38:40]  # Large font page offset. This will be the last data offset if there are none.
        record += struct.pack('>H', image_data_offset)      # [40:42]  # Image offset. This will be the last data offset if there are none.
        record += struct.pack('>H', link_offset)            # [42:44]  # Links offset. This will be the last data offset if there are none.
        record += struct.pack('>H', meta_data_offset)       # [44:46]  # Metadata offset. This will be the last data offset if there are none.
        record += struct.pack('>H', 0)                      # [46:48]  # Unknown.
        record += struct.pack('>H', last_data_offset)       # [48:50]  # Footnote offset. This will be the last data offset if there are none.
        record += struct.pack('>H', last_data_offset)       # [50:52]  # Sidebar offset. This will be the last data offset if there are none.
        record += struct.pack('>H', last_data_offset)       # [52:54]  # Last data offset.

        for i in range(54, 132, 2):
            record += struct.pack('>H', 0)                  # [54:132]

        return record
