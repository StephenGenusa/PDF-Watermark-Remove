#!/usr/bin/env python

"""
Copyright © 2019 by Stephen Genusa
Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
1. Redistributions of source code must retain the above copyright notice,
this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
this list of conditions and the following disclaimer in the documentation
and/or other materials provided with the distribution.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.
"""

import os
import sys
import operator

from pdfrw import PdfReader, PdfWriter, IndirectPdfDict


"""
Strip watermarks, prop page and write clean file
"""
def process_pdf_file(inputFilename):

    try:
        print (f'Checking {inputFilename}')
        skip_this_page = False
        total_watermarks_skipped = 0

        try:
            reader = PdfReader(inputFilename)
        except:
            pass
        else:
            writer = PdfWriter()

            wm_width = 0
            page_count = 0
            counts = dict()
            sample_pages = []

            # Look through all pages for a potential primary watermark item
            if reader is not None:
                for idx, page in enumerate(reader.pages):
                    if '/Resources' in page and '/XObject' in page['/Resources']:
                        for xobj in page['/Resources']['/XObject']:
                            # Warning: Masks may or may not indicate WM presence
                            if '/Mask' in page['/Resources']['/XObject'][str(xobj)]:
                                if '/Width' in page['/Resources']['/XObject'][str(xobj)]:
                                    cur_width = int(page['/Resources']['/XObject'][str(xobj)]['/Width'])
                                    counts[cur_width] = counts.get(cur_width, 0) + 1
                                    sample_pages.append(idx+1)
                    page_count += 1
                if counts:
                    wm_width = max(counts, key=lambda key: counts[key])
                    if counts[wm_width] != page_count and len(counts) < 4:
                        print('*' * 40)
                        print(f'* Potential watermarks found but only occurs in {counts[wm_width]} of {page_count} pages')
                        print(f'* Sample pages: {sample_pages[0:9]}')
                        counts = sorted(counts.items(), reverse=True, key=lambda x: x[1])
                        print(f'* {counts}')
                        print('*' * 40)
                        wm_width = 0

                # Process all pages removing prop pages and watermark objects
                for idx, page in enumerate(reader.pages):
                    skip_this_page = False

                    # ************** Prop Pages **************
                    # Google
                    try:
                        skip_this_page = 'google' in page['/Annots'][0]['/A']['/URI']
                    except:
                        pass

                    # HathiTrust
                    # Looks like this may need another method for checking for the existence of the JxCBE
                    if not skip_this_page:
                        try:
                            if idx == 0:
                                skip_this_page = '/JxCBE' in page['/Resources']['/XObject']['/CLC']['/Resources']['/XObject']
                        except:
                            pass
                    if not skip_this_page:
                        try:
                            if idx == 0:
                                skip_this_page = '/JxCBE' in page['/Resources']['/XObject']['/CCA']['/Resources']['/XObject']
                        except:
                            pass

                    # Internet Archive / Microsoft
                    if not skip_this_page:
                        try:
                            if idx == 2:
                                skip_this_page = page['/Resources']['/XObject']['/Im001']['/Length'] == '8420'
                        except:
                            pass

                    # ************** Watermarks **************
                    # Dump Google watermarks
                    if '/Resources' in page and '/XObject' in page['/Resources'] and '/Wm' in page['/Resources']['/XObject']:
                        junk = page['/Resources']['/XObject'].pop('/Wm')
                        total_watermarks_skipped += 1
                    if '/Resources' in page and '/XObject' in page['/Resources']:
                        for xobj in page['/Resources']['/XObject']:
                            if '/Mask' in page['/Resources']['/XObject'][str(xobj)]:
                                if '/Width' in page['/Resources']['/XObject'][str(xobj)]:
                                    cur_width = int(page['/Resources']['/XObject'][str(xobj)]['/Width'])
                                    if cur_width == wm_width:
                                        junk = page['/Resources']['/XObject'].pop(str(xobj))
                                        total_watermarks_skipped += 1
                        for xobj in page['/Resources']['/XObject']:
                            if page['/Resources']['/XObject'][str(xobj)]['/Width'] == '156':
                                junk = page['/Resources']['/XObject'].pop(str(xobj))
                                total_watermarks_skipped += 1

                    # Dump HathiTrust watermarks
                    if '/Resources' in page and '/XObject' in page['/Resources'] and \
                       '/CBJ' in page['/Resources']['/XObject'] and \
                        '/Resources' in page['/Resources']['/XObject']['/CBJ'] and \
                        '/XObject' in page['/Resources']['/XObject']['/CBJ']['/Resources'] and \
                        '/PxCBA' in page['/Resources']['/XObject']['/CBJ']['/Resources']['/XObject']:
                            junk = page['/Resources']['/XObject']['/CBJ']['/Resources']['/XObject'].pop('/PxCBA')
                            total_watermarks_skipped += 1
                    if '/Resources' in page and '/XObject' in page['/Resources'] and \
                       '/CBJ' in page['/Resources']['/XObject'] and \
                        '/Resources' in page['/Resources']['/XObject']['/CBJ'] and \
                        '/XObject' in page['/Resources']['/XObject']['/CBJ']['/Resources'] and \
                        '/PxCBF' in page['/Resources']['/XObject']['/CBJ']['/Resources']['/XObject']:
                            junk = page['/Resources']['/XObject']['/CBJ']['/Resources']['/XObject'].pop('/PxCBF')
                            total_watermarks_skipped += 1
                    if '/Resources' in page and '/XObject' in page['/Resources'] and \
                       '/CBJ' in page['/Resources']['/XObject'] and \
                        '/Resources' in page['/Resources']['/XObject']['/CBJ'] and \
                        '/XObject' in page['/Resources']['/XObject']['/CBJ']['/Resources'] and \
                        '/PxCBG' in page['/Resources']['/XObject']['/CBJ']['/Resources']['/XObject']:
                            junk = page['/Resources']['/XObject']['/CBJ']['/Resources']['/XObject'].pop('/PxCBG')
                            total_watermarks_skipped += 1

                    # Add the page unless it's the prop page
                    if not skip_this_page and page.Contents is not None:
                        writer.addpage(page)

                # Copy and clean up the metadata
                if reader['/Info']:
                    new_meta_dict = {}
                    for info in reader['/Info']:
                        if '/Producer' not in info:
                            new_meta_dict[info] = reader['/Info'].get(info)
                    writer.trailer.Info = IndirectPdfDict(new_meta_dict)

                # Write the new file if cleanup was necessary
                if total_watermarks_skipped or (len(reader.pages) != len(writer.pagearray)):
                    filename, file_extension = os.path.splitext(inputFilename)
                    os.rename(inputFilename, filename + '.bak')
                    writer.write(inputFilename)
                    if len(reader.pages) != len(writer.pagearray):
                        print(f'  {len(reader.pages) - len(writer.pagearray)} pages deleted', )
                    if total_watermarks_skipped:
                        print(f'  {total_watermarks_skipped} page watermark references removed')
                    print(f'  Clean file written to {inputFilename}')

    except Exception as e:
        print('Exception: ', e)


def main(root_path):
    for root, dirs, files in os.walk(root_path):
        for filename in files:
            file_name, file_extension = os.path.splitext(filename)
            if file_extension.lower() == '.pdf':
                process_pdf_file(os.path.join(root, filename))


if __name__ == '__main__':
    if len(sys.argv) > 1:
        start_path = sys.argv[1]
    else:
        start_path = '/Downloaded PDFs'
    if os.path.exists(start_path):
        main(start_path)
    else:
        print ('Starting path', start_path, 'not found')
