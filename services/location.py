#!/usr/bin/env python3
"""省/市拼接与拆分工具。"""
from utils import CHINA_CITIES


def compose_location(province, city):
    if province and city:
        return province if province == city else f"{province}{city}"
    return province or city or None


def split_location(location):
    if not location:
        return '', ''
    for province, cities in CHINA_CITIES.items():
        if location == province:
            return province, (province if province in cities else '')
        for city in cities:
            if location == f"{province}{city}":
                return province, city
    for province, cities in CHINA_CITIES.items():
        if location in cities:
            return province, location
    return location, ''


def is_remote_qualified(work, household, spouse):
    cond1 = bool(work) and bool(household) and work != household
    cond2 = (not spouse) or (spouse != work)
    return cond1 and cond2
