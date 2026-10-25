"""Read-only selection over complete catalog records, never customer text."""
import math
import re
from support_agent.domain.identity import ReadError


def search_index(index, text):
    # The reviewed REST contract is unpaginated. Traverse the complete returned
    # collection; never invent cursor parameters or silently return a first page.
    matches=[p for p in index if text.casefold() in p['name'].casefold()]
    return {'products':matches,'total_count':len(index),'matching_count':len(matches),'complete':True}


def option_values(product):
    return {key:list(dict.fromkeys(i['options'][key] for i in product['items'] if key in i['options']))
                for i in product['items'] for key in i['options']}


def rank_variants(product, filters=None, numeric_filters=None, preferences=None, descending=False):
    filters=filters or {};numeric_filters=numeric_filters or [];preferences=preferences or []
    vocabulary=option_values(product)
    def invalid():
        raise ReadError('invalid_catalog_criteria')
    if not isinstance(filters,dict) or any(k not in vocabulary or v not in vocabulary[k] for k,v in filters.items()):
        invalid()
    comparisons={'lt':lambda a,b:a<b,'lte':lambda a,b:a<=b,'eq':lambda a,b:a==b,
                 'gte':lambda a,b:a>=b,'gt':lambda a,b:a>b}
    for row in numeric_filters:
        if (set(row)!={'option','operator','value','unit'} or not isinstance(row['option'],str) or row['option'] not in vocabulary or
                not isinstance(row['operator'],str) or row['operator'] not in comparisons or not isinstance(row['unit'],str) or not row['unit'] or
                type(row['value']) not in (float,int) or not math.isfinite(row['value'])):
            invalid()
    for row in preferences:
        if (set(row)!={'option','values'} or not isinstance(row['option'],str) or row['option'] not in vocabulary or
                not isinstance(row['values'],list) or not row['values'] or
                any(v not in vocabulary[row['option']] for v in row['values'])):
            invalid()
    def numeric(value,unit):
        match=re.fullmatch(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*'+re.escape(unit),value,re.IGNORECASE)
        if not match:invalid()  # A malformed/unit-mismatched criterion is not out of stock.
        number=float(match[1])
        if not math.isfinite(number):invalid()
        return number
    matches=[]
    for item in product['items']:
        if any(item['options'].get(k)!=v for k,v in filters.items()):continue
        if all(comparisons[r['operator']](numeric(item['options'].get(r['option'],''),r['unit']),r['value'])
               for r in numeric_filters):matches.append(item)
    available=[i for i in matches if i['available']]
    def score(item):
        ranks=[]
        for r in preferences:
            value=item['options'].get(r['option'])
            ranks.append(r['values'].index(value) if value in r['values'] else len(r['values']))
        return (*ranks,-item['price'] if descending else item['price'])
    available.sort(key=score)
    return {'product_id':product['product_id'],'option_values':vocabulary,'candidates':available,
            'matching_count':len(matches),'available_matching_count':len(available),
            'unavailable_matching_count':len(matches)-len(available),'returned_count':len(available),
            'complete':True,'ranking':'ordered preferences, then price descending' if descending else 'ordered preferences, then price ascending'}
