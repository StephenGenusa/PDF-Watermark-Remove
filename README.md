# Remove PDF Watermarks from Public Domain Books #

This is a work in progress. Support for pubic domain books downloaded from:

- Google
- HathiTrust
- Archive.org/Microsoft

This removes the info page and watermarks from every page. Starting parameter is a directory path name, enclosed in quotes if there are any spaces. 

All PDFs in that path, and any subpaths, will be processed. This will create a [filename]_clean.pdf for each PDF that has either the info page removed and/or watermarks removed.

## Example Program Usage ##

**Process a directory**
<pre>
remove_google_wm.py '/Downloaded PDFs'
</pre>


## ToDo List ##
- Remove prop pages on older Google PDFs
- Test more HathiTrust PDFs
- Test more archive.org/Microsoft PDFs
