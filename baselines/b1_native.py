"""B1: camera-native raw bytes (the CFA image's strip/tile bytes only, previews excluded)."""
import tifffile


def raw_bytes(path):
    with tifffile.TiffFile(path) as tf:
        best = None
        for page in tf.pages:
            for sub in [page] + list(getattr(page, "pages", []) or []):
                if getattr(sub, "photometric", None) is None:
                    continue
                if int(sub.photometric) == 32803:  # CFA
                    n = sum(sub.databytecounts)
                    if best is None or n > best:
                        best = n
        return best
