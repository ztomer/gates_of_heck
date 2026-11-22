* lib/killtree.py defers its annotations, so a consumer that patches subprocess.Popen with a function can import it on Python 3.13 (GOH.KILLTREEPOPEN).
