import tempfile
import unittest
from pathlib import Path

from extract_resources import extract


class ExtractionSafety(unittest.TestCase):
    def test_unknown_firmware_writes_nothing(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as directory:
            root = Path(directory)
            binary = root / 'input'
            binary.write_bytes(b'unsupported')
            output = root / 'output'
            with self.assertRaisesRegex(ValueError, 'Unsupported firmware'):
                extract(binary, output)
            self.assertFalse(output.exists())

    def test_git_tree_refused_before_reading_firmware(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as directory:
            root = Path(directory)
            (root / '.git').mkdir()
            with self.assertRaisesRegex(ValueError, 'outside Git'):
                extract(root / 'missing', root / 'output')
            self.assertFalse((root / 'output').exists())

    def test_existing_output_preserved(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as directory:
            root = Path(directory)
            output = root / 'output'
            output.mkdir()
            sentinel = output / 'keep'
            sentinel.write_text('keep')
            with self.assertRaisesRegex(ValueError, 'new private'):
                extract(root / 'missing', output)
            self.assertEqual(sentinel.read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
