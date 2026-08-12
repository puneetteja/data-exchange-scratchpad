import xml.etree.ElementTree as ET
from typing import List, Dict, Optional
from pathlib import Path


class PaymentFileCategorizer:
    """Categorizes payment files based on PmtTpInf tag structure, and flags
    Pain1 Relay (P1R) files identified by a <FwdgAgt> element in GrpHdr."""

    CATEGORIES = {
        'SEPA_NORMAL': 'SEPA normal payment file',
        'SEPA_INSTANT': 'SEPA Instant payment file',
        'SEPA_NON_URGENT': 'SEPA non urgent payment file',
        'NON_SEPA_URGENT': 'Non-SEPA urgent payment file',
        'NON_SEPA_NON_URGENT': 'Non-SEPA non-urgent payment file',

        'SEPA_NORMAL_P1R': 'Pain1 Relay - SEPA normal payment file',
        'SEPA_INSTANT_P1R': 'Pain1 Relay - SEPA Instant payment file',
        'NON_SEPA_NON_URGENT_P1R': 'Pain1 Relay - Non-SEPA non-urgent payment file',

        'PAIN008_CORE': 'PAIN008 Core payment file',
        'PAIN008_B2B': 'PAIN008 B2B payment file',
        'MULTIPLE_CATEGORIES': 'Multiple payment categories in file',
        'UNKNOWN': 'Unknown payment file type'
    }

    # Categories that have a defined Pain1 Relay (P1R) counterpart.
    # Any other base category found in a relay file is left unchanged,
    # since no P1R variant is defined for it.
    P1R_ELIGIBLE = {
        'SEPA_NORMAL': 'SEPA_NORMAL_P1R',
        'SEPA_INSTANT': 'SEPA_INSTANT_P1R',
        'NON_SEPA_NON_URGENT': 'NON_SEPA_NON_URGENT_P1R',
    }

    def __init__(self, filepath: str):
        """Initialize with file path."""
        self.filepath = filepath
        self.tree = None
        self.root = None
        self.namespace = {}
        self.is_relay = False

    def parse_file(self) -> bool:
        """Parse the XML file and extract namespace."""
        try:
            self.tree = ET.parse(self.filepath)
            self.root = self.tree.getroot()

            # Extract namespace if present
            if self.root.tag.startswith('{'):
                ns = self.root.tag[1:self.root.tag.index('}')]
                self.namespace = {'ns': ns}

            return True
        except ET.ParseError as e:
            print(f"Error parsing XML file: {e}")
            return False
        except FileNotFoundError:
            print(f"File not found: {self.filepath}")
            return False

    def find_pmttpinf_elements(self) -> List[ET.Element]:
        """Find all PmtTpInf elements in the document."""
        if self.namespace:
            return self.root.findall('.//ns:PmtTpInf', self.namespace)
        else:
            return self.root.findall('.//PmtTpInf')

    def detect_relay(self) -> bool:
        """Detect whether the file is a Pain1 Relay (P1R) file, identified
        by the presence of a <FwdgAgt> element (typically under GrpHdr)."""
        if self.namespace:
            fwdg_agt = self.root.find('.//ns:GrpHdr/ns:FwdgAgt', self.namespace)
            if fwdg_agt is None:
                # Fall back to searching anywhere in the document, in case
                # FwdgAgt appears outside GrpHdr in some file variants.
                fwdg_agt = self.root.find('.//ns:FwdgAgt', self.namespace)
        else:
            fwdg_agt = self.root.find('.//GrpHdr/FwdgAgt')
            if fwdg_agt is None:
                fwdg_agt = self.root.find('.//FwdgAgt')

        return fwdg_agt is not None

    def extract_pmttpinf_info(self, pmttpinf: ET.Element) -> Dict[str, Optional[str]]:
        """Extract relevant information from a PmtTpInf element."""
        info = {
            'SvcLvl_Cd': None,
            'LclInstrm_Cd': None,
            'SeqTp': None
        }

        # Extract SvcLvl/Cd
        if self.namespace:
            svc_lvl = pmttpinf.find('ns:SvcLvl/ns:Cd', self.namespace)
            lcl_instrm = pmttpinf.find('ns:LclInstrm/ns:Cd', self.namespace)
            seq_tp = pmttpinf.find('ns:SeqTp', self.namespace)
        else:
            svc_lvl = pmttpinf.find('SvcLvl/Cd')
            lcl_instrm = pmttpinf.find('LclInstrm/Cd')
            seq_tp = pmttpinf.find('SeqTp')

        if svc_lvl is not None:
            info['SvcLvl_Cd'] = svc_lvl.text
        if lcl_instrm is not None:
            info['LclInstrm_Cd'] = lcl_instrm.text
        if seq_tp is not None:
            info['SeqTp'] = seq_tp.text

        return info

    def categorize_pmttpinf(self, info: Dict[str, Optional[str]]) -> str:
        """Categorize based on extracted information. If the file was
        detected as a Pain1 Relay file, the P1R variant is returned for
        categories that have one defined."""
        svc_cd = info['SvcLvl_Cd']
        lcl_cd = info['LclInstrm_Cd']
        seq_tp = info['SeqTp']

        base_category = 'UNKNOWN'

        # PAIN008 Core payment file
        if svc_cd == 'SEPA' and lcl_cd == 'CORE' and seq_tp is not None:
            base_category = 'PAIN008_CORE'

        # PAIN008 B2B payment file
        elif svc_cd == 'SEPA' and lcl_cd == 'B2B' and seq_tp is not None:
            base_category = 'PAIN008_B2B'

        # SEPA Instant payment file
        elif svc_cd == 'SEPA' and lcl_cd == 'INST':
            base_category = 'SEPA_INSTANT'

        # SEPA normal payment file
        elif svc_cd == 'SEPA' and lcl_cd is None:
            base_category = 'SEPA_NORMAL'

        # Non-SEPA non urgent payment file
        elif svc_cd == 'NURG':
            base_category = 'NON_SEPA_NON_URGENT'

        # Non-SEPA urgent payment file
        elif svc_cd == 'URGP':
            base_category = 'NON_SEPA_URGENT'

        # Apply Pain1 Relay (P1R) variant if applicable
        if self.is_relay and base_category in self.P1R_ELIGIBLE:
            return self.P1R_ELIGIBLE[base_category]

        return base_category

    def analyze(self) -> Dict:
        """Analyze the file and return categorization results."""
        if not self.parse_file():
            return {'error': 'Failed to parse file'}

        self.is_relay = self.detect_relay()

        pmttpinf_elements = self.find_pmttpinf_elements()

        if not pmttpinf_elements:
            return {
                'file': self.filepath,
                'pmttpinf_count': 0,
                'is_relay': self.is_relay,
                'file_category': 'UNKNOWN',
                'categories': {},
                'message': 'No PmtTpInf elements found'
            }

        results = {
            'file': self.filepath,
            'pmttpinf_count': len(pmttpinf_elements),
            'is_relay': self.is_relay,
            'categories': {},
            'details': []
        }

        # Collect all categories
        all_categories = []

        for idx, pmttpinf in enumerate(pmttpinf_elements, 1):
            info = self.extract_pmttpinf_info(pmttpinf)
            category = self.categorize_pmttpinf(info)

            all_categories.append(category)

            # Count categories
            if category not in results['categories']:
                results['categories'][category] = 0
            results['categories'][category] += 1

            # Store details
            results['details'].append({
                'index': idx,
                'category': category,
                'category_name': self.CATEGORIES[category],
                'info': info
            })

        # Check if all categories are the same
        unique_categories = set(all_categories)

        if len(unique_categories) == 1:
            # All transactions have the same category
            results['file_category'] = all_categories[0]
        else:
            # Multiple different categories found
            results['file_category'] = 'MULTIPLE_CATEGORIES'

        return results


def main():
    """Example usage of the PaymentFileCategorizer."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python script.py <xml_file_path>")
        print("\nExample:")
        print("  python script.py payment_file.xml")
        return

    filepath = sys.argv[1]

    categorizer = PaymentFileCategorizer(filepath)
    results = categorizer.analyze()

    # Output the three variables for shell script capture
    if 'error' not in results:
        file_category = results.get('file_category', 'UNKNOWN')
        pmttpinf_count = results.get('pmttpinf_count', 0)
        category_description = PaymentFileCategorizer.CATEGORIES.get(file_category, 'Unknown')

        # Output in format: category count description
        print(f"{file_category} {pmttpinf_count} {category_description}")
    else:
        print(f"ERROR 0 {results['error']}")


if __name__ == "__main__":
    main()
