"""Select bounded family-history passages; fingerprint the complete source locally."""
from dataclasses import asdict, dataclass
import re
from .radiology_artifacts import digest

FAMILY = re.compile(r'\b(?:Familienanamnese|family\s+history|famili\w*|familial|family)\b', re.I)
KINSHIP = re.compile(r'\b(?:Mutter|Vater|Tochter|Sohn|Schwestern?|Bruder|Brüder|Geschwister|Großmutter|Grossmutter|Großvater|Grossvater|Großeltern|Grosseltern|Tante|Onkel|Nichte|Neffe|Enkel\w*|Halb(?:bruder|schwester)|half[- ](?:brother|sister|sibling)|Cousin\w*|Kusine|Verwandt\w*|Ehefrau|Ehemann|mother|father|daughters?|sons?|sisters?|brothers?|siblings?|grandmother|grandfather|grandparents?|aunts?|uncles?|nieces?|nephews?|grandchild(?:ren)?|cousin|relative|wife|husband)\b', re.I)
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
            consumed=set()
            for index in sorted(indices):
                if index in consumed:continue
                # Narrative selection is sentence-sized, not an entire Anamnese.
                block=lines[index]
                following=index+1
                # Preserve wrapped sentence continuation, bounded by blank lines,
                # explicit headings, and sentence completion. Never invent text.
                while (not re.search(r'[.!?]\s*$',block) and following<len(lines)
                       and lines[following].strip() and not BOUNDARY.match(lines[following])
                       and not HEADING.match(lines[following])):
                    consumed.add(following)
                    block+='\n'+lines[following];following+=1
                    if len(block)>4000:raise ValueError('Family-history passage exceeds focused input limit')
                    if re.search(r'[.!?]\s*$',lines[following-1]):break
                for passage in re.split(r'(?<=[.!?])\s+', block.strip()):
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
