"""Build time only: download the models Docling and RapidOCR need into the image.

Builds the same pipeline as app/ingest/parse.py (layout, table structure, RapidOCR)
without importing the app, so the Docker layer that runs this depends only on the
installed dependencies: a code change doesn't download ~540 MB again.
"""

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

options = PdfPipelineOptions(do_ocr=True, ocr_options=RapidOcrOptions(), do_table_structure=True)
converter = DocumentConverter(
    format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
)
converter.initialize_pipeline(InputFormat.PDF)
