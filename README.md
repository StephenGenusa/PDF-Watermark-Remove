# Remove PDF Watermarks from Public Domain Books #

This removes the info page and google watermarks from every page. Starting parameter is a path name, enclosed in quotes if there are any spaces. All PDFs in that path, and any subpaths, will be processed. This will create a [filename]_clean.pdf for each PDF that has either the info page removed and/or watermarks removed.

## Example Program Usage ##

**Process a directory**
<pre>
remove_google_wm.py '/Downloaded PDFs'
</pre>


## ToDo List ##
- add support for HathiTrust downloads
- add support for Microsoft downloads
- add support for archive.org downloads
