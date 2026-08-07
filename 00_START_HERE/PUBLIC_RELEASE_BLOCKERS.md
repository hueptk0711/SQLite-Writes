# Public-release checklist

The internal release candidate is reproducible but must not be uploaded as a
public artifact until the following ownership and license decisions are closed.

- [ ] The code owner selects and adds a code license.
- [ ] The licenses and redistribution terms of every included database/dataset
      file are reviewed and documented.
- [ ] The model name, exact revision/hash, license, quantization, and access
      instructions are documented; model weights are not included.
- [ ] Author names, affiliations, contact address, repository URL, and archival
      DOI are filled in. Use
      `../PAPER_WRITING/01_manuscript/submission/ieee_access_latex_20260801/AUTHOR_METADATA_TEMPLATE_VI.md`.
- [ ] A `CITATION.cff` is finalized only after the author list and public release
      title are final. A non-public placeholder is available at
      `../PAPER_WRITING/01_manuscript/submission/ieee_access_latex_20260801/CITATION.cff.template`.
- [ ] Any artifact that cannot be redistributed is replaced by a checksum and
      an official acquisition instruction.

These items require decisions by the project/data owners. The release builder
therefore labels its output `internal_release_candidate`, not `public_release`.
