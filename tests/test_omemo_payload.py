import xml.etree.ElementTree as ET

import pytest

from xlii.omemo_payload import sealed_recipient_keys


class _Encrypted:
    def __init__(self, xml):
        self.xml = xml


def test_sealed_recipient_keys_extracts_oldmemo_key_ids():
    xml = ET.fromstring(
        """
        <message>
          <encrypted xmlns="eu.siacs.conversations.axolotl">
            <header sid="123">
              <key rid="111" prekey="true">abc</key>
              <key rid="222">def</key>
            </header>
          </encrypted>
        </message>
        """
    )

    assert sealed_recipient_keys(_Encrypted(xml)) == [
        ("111", "prekey"),
        ("222", "msg"),
    ]


def test_sealed_recipient_keys_returns_empty_for_keyless_fallback():
    xml = ET.fromstring(
        """
        <message>
          <body>This message is OMEMO encrypted.</body>
        </message>
        """
    )

    assert sealed_recipient_keys(_Encrypted(xml)) == []


@pytest.mark.parametrize("encrypted", [object(), _Encrypted(None)])
def test_sealed_recipient_keys_rejects_uninspectable_stanza(encrypted):
    with pytest.raises(ValueError):
        sealed_recipient_keys(encrypted)


def test_sealed_recipient_keys_rejects_xml_without_findall():
    with pytest.raises(ValueError):
        sealed_recipient_keys(_Encrypted(object()))
