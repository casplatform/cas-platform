#!/usr/bin/env python3
import datetime
import xml.etree.ElementTree as ET
from xml.dom import minidom

urls = [
    "https://www.casplatform.com/",
    "https://www.casplatform.com/catalog.html",
    "https://www.casplatform.com/pricing",
    "https://www.casplatform.com/documentation",
    "https://www.casplatform.com/portal",
    "https://www.casplatform.com/legal",
    "https://www.casplatform.com/terms",
    "https://www.casplatform.com/privacy",
]

root = ET.Element("urlset")
root.set("xmlns", "http://www.sitemaps.org/schemas/sitemap/0.9")

today = datetime.datetime.now().strftime("%Y-%m-%d")

for url in urls:
    url_elem = ET.SubElement(root, "url")
    ET.SubElement(url_elem, "loc").text = url
    ET.SubElement(url_elem, "lastmod").text = today
    ET.SubElement(url_elem, "changefreq").text = "weekly"
    ET.SubElement(url_elem, "priority").text = "0.8"

# Güzel formatlı XML
xml_str = ET.tostring(root, encoding="unicode")
pretty_xml = minidom.parseString(xml_str).toprettyxml(indent="  ")

with open("sitemap.xml", "w", encoding="utf-8") as f:
    f.write(pretty_xml)

print("✅ Güncellenmiş sitemap.xml oluşturuldu!")
print(f"   Toplam URL sayısı: {len(urls)}")
print("   Dosya: /opt/cas/static/sitemap.xml")
