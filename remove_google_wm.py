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

from pdfrw import PdfReader, PdfWriter, IndirectPdfDict


"""
Append _clean to the filename
"""
def get_clean_filename (base_filename):
    filename, file_extension = os.path.splitext(base_filename)
    return filename + '_clean' + file_extension


"""
Strip watermarks, prop page and write clean file
"""
def process_pdf_file(inputFilename):
    try:
        print "Processing", inputFilename
        google_page_skipped = False
        total_watermarks_skipped = 0
            
        reader = PdfReader(inputFilename)
        writer = PdfWriter()
        
        # Determine if there's a google prop page
        try:
            google_intro_page = 'google' in reader.pages[0]['/Annots'][0]['/A']['/URI']
        except:
            google_intro_page = False
        
        # Do the page copy except for google prop
        for index, page in enumerate(reader.pages):
            # Dump the watermarks
            if '/Resources' in page and '/XObject' in page['/Resources'] and '/Wm' in page['/Resources']['/XObject']:
                junk = page['/Resources']['/XObject'].pop('/Wm')
                total_watermarks_skipped += 1
             
            # Add the page unless it's the prop page
            if (google_intro_page and index > 0) and page.Contents is not None:
                writer.addpage(page)
            else:
                if google_intro_page:
                    google_page_skipped = True
        
        # Copy and clean up the metadata
        if reader['/Info']:
            new_meta_dict = {}
            for info in reader['/Info']:
                if '/Producer' in info:
                    continue
                else:
                    new_meta_dict[info] = reader['/Info'].get(info)
            writer.trailer.Info = IndirectPdfDict(new_meta_dict)    

        # Write the new file if cleanup was necessary
        if google_page_skipped or total_watermarks_skipped:
            writer.write(get_clean_filename(inputFilename))
            if google_page_skipped:
                print "  google prop page skipped"
            if total_watermarks_skipped:
                print " ", total_watermarks_skipped, "page watermark references removed"
            print "  _clean file written to", get_clean_filename(inputFilename)

    except:
        pass


def main(root_path):
    for root, dirs, files in os.walk(root_path):
        for file in files:
            file_name, file_extension = os.path.splitext(file)
            if file_extension.lower() == '.pdf' and file.find('_clean') == -1 and \
               not os.path.isfile(os.path.join(root, get_clean_filename(file))):
                process_pdf_file(os.path.join(root, file))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        start_path = sys.argv[1]
    else:
        start_path = "/Downloaded PDFs"
    if os.path.exists(start_path):
        main(start_path)
    else:
        print "Starting path", start_path, "not found"
