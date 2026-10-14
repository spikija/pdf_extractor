"""Select bounded family-history passages; fingerprint the complete source locally."""
from dataclasses import asdict, dataclass
import re
from .radiology_artifacts import digest

FAMILY = re.compile(r'\b(?:Familienanamnese|family\s+history|famili\w*|familial|family)\b', re.I)
KINSHIP = re.compile(r'\b(?:Mutter|Vater|Tochter|Sohn|Schwester|Bruder|Großmutter|Grossmutter|Großvater|Grossvater|Tante|Onkel|Nichte|Neffe|Enkel\w*|Halb(?:bruder|schwester)|half[- ](?:brother|sister|sibling)|Cousin\w*|Kusine|Verwandt\w*|Ehefrau|Ehemann|mother|father|daughter|son|sister|brother|sibling|grandmother|grandfather|grandparent|aunt|uncle|niece|nephew|grandchild|cousin|relative|wife|husband)\b', re.I)
HEADING = re.compile(r'^\s*(?:Familienanamnese|Family\s+history)\s*:?(.*)$', re.I)
BOUNDARY = re.compile(r'^\s*(?:Eigenanamnese|Sozialanamnese|Medikation|Medikamente|Labor\w*|Genetik|Neurologischer\s+Status|Diagnosen|Therapie\w*|Anamnese)\s*:', re.I)


@dataclass(frozen=True)
class FamilyHistoryInput:
    source_document_id: int
    section_id: str
    page_number: int
    text: str
    family_context: bool = False

    def context(self):
        return asdict(self)


def select_family_history_inputs(bundle):
    selected = []
    for document in bundle.documents:
        for section in document.sections:
            lines = section.text.splitlines()
            indices = set()
            family_indices = set()
            if section.heading and HEADING.match(section.heading):
                for index, line in enumerate(lines):
                    if BOUNDARY.match(line):
                        break
                    indices.add(index)
                    family_indices.add(index)
            for index, line in enumerate(lines):
                if FAMILY.search(line) or KINSHIP.search(line):
                    indices.add(index)
                if HEADING.match(line):
                    family_indices.add(index)
                    # Family-specific heading may introduce a short paragraph.
                    for following in range(index + 1, min(index + 8, len(lines))):
                        if not lines[following].strip() or BOUNDARY.match(lines[following]):
                            break
                        indices.add(following)
                        family_indices.add(following)
            for index in sorted(indices):
                # Narrative selection is sentence-sized, not an entire Anamnese.
                for passage in re.split(r'(?<=[.!?])\s+', lines[index].strip()):
                    if index not in family_indices and not (FAMILY.search(passage) or KINSHIP.search(passage)):
                        continue
                    if len(passage) > 4000:
                        raise ValueError('Family-history passage exceeds focused input limit')
                    if passage:
                        selected.append(FamilyHistoryInput(document.source_document_id,
                            section.section_id, section.page_start, passage, index in family_indices))
    return selected


def source_manifest(bundle):
    """Also invalidate an empty artifact when previously unselected sources change."""
    return digest(bundle.to_dict())
