import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import install_local_yamnet as installer


class YamnetInstallerTest(unittest.TestCase):
    def test_verify_and_never_replace_destination(self):
        model=b'fake onnx model'
        with tempfile.TemporaryDirectory() as tmp, patch.object(installer,'SIZE',len(model)), patch.object(installer,'SHA256',hashlib.sha256(model).hexdigest()), patch.object(installer.urllib.request,'urlopen',return_value=io.BytesIO(model)):
            dest=Path(tmp)/'model.onnx'
            installer.install(dest)
            self.assertEqual(dest.read_bytes(),model)
            installer.install(dest)
            dest.write_bytes(b'different')
            with self.assertRaises(FileExistsError):installer.install(dest)
            self.assertEqual(dest.read_bytes(),b'different')

    def test_bad_checksum_leaves_no_model(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(installer,'SIZE',3), patch.object(installer,'SHA256','0'*64), patch.object(installer.urllib.request,'urlopen',return_value=io.BytesIO(b'bad')):
            dest=Path(tmp)/'model.onnx'
            with self.assertRaises(ValueError):installer.install(dest)
            self.assertFalse(dest.exists())
            self.assertEqual(list(Path(tmp).iterdir()),[])

if __name__ == '__main__': unittest.main()
