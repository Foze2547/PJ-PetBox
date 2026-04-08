import re

def test_room_logic(text):
    # The logic from thai_speech_rec.py
    room_pattern_2_201 = r"(ห้อง\s*)?(2|สอง)\s*(2|สอง)\s*(0|ศูนย์)\s*(1|หนึ่ง)"
    if re.search(room_pattern_2_201, text):
        return "จุดหมายคือห้อง 2-201 ซึ่งอยู่ที่ชั้น 2 ของตึก Drawing คณะวิศวกรรมศาสตร์ มหาวิทยาลัยเชียงใหม่ กรุณาไปที่ตึก Drawing ก่อน จากนั้นขึ้นไปชั้น 2 และมองหาป้ายห้อง 2-201"

    room_pattern_201 = r"(ห้อง\s*)?(2|สอง)\s*(0|ศูนย์)\s*(1|หนึ่ง)"
    if re.search(room_pattern_201, text):
        return "จุดหมายคือห้อง 201 ตึก 30 ปี กรุณาไปที่ตึก 30 ปี จากนั้นขึ้นไปชั้น 2 แล้วมองหาป้ายห้อง 201"
    
    return "No match"

test_cases = [
    "ห้องสองสองศูนย์หนึ่ง",
    "สองสองศูนย์หนึ่ง",
    "2201",
    "ห้อง 2201",
    "ห้องสองศูนย์หนึ่ง",
    "สองศูนย์หนึ่ง",
    "201",
    "ห้อง 201"
]

for tc in test_cases:
    print(f"Input: {tc} -> Output: {test_room_logic(tc)}")
