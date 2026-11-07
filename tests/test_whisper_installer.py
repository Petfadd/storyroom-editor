import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tools import install_local_whisper as installer

class InstallerTest(unittest.TestCase):
    def test_verified_download_and_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / 'model'
            pieces = {f'model.bin.part-{i:02}': bytes([i]) * 3 for i in range(11)}
            pieces.update({'config.json': b'config', 'tokenizer.json': b'tokenizer', 'vocabulary.txt': b'vocab'})
            other = {k: hashlib.sha256(pieces[k]).hexdigest() for k in ('config.json','tokenizer.json','vocabulary.txt')}
            model = b''.join(pieces[f'model.bin.part-{i:02}'] for i in range(11))
            def download(name, out, h): out.write(pieces[name]);h.update(pieces[name])
            with patch.object(installer,'OTHER',other), patch.object(installer,'MODEL_HASH',hashlib.sha256(model).hexdigest()), patch.object(installer,'MODEL_SIZE',len(model)), patch.object(installer,'download',download):
                installer.install(dest)
                self.assertTrue(installer.valid(dest))
                installer.install(dest)
                (dest/'config.json').write_bytes(b'user content')
                with self.assertRaises(FileExistsError): installer.install(dest)
                self.assertEqual((dest/'config.json').read_bytes(),b'user content')

    def test_bad_download_leaves_no_partial_install(self):
        with tempfile.TemporaryDirectory() as directory:
            dest=Path(directory)/'model'
            def bad_download(name,out,h):out.write(b'bad');h.update(b'bad')
            with patch.object(installer,'download',bad_download):
                with self.assertRaisesRegex(ValueError,'Checksum'):installer.install(dest)
            self.assertFalse(dest.exists())
            self.assertEqual(list(Path(directory).iterdir()),[])

if __name__ == '__main__':unittest.main()
