"""Replay a receipt's cost exposure through immutable quantity movements.

Fractions are attribution ratios, never floating-point money. Historical entries
are not rewritten. Production weights are recorded independently of rounded cost,
so even zero-cost inputs and mixed, multi-output production remain traceable.
"""
from collections import defaultdict
from fractions import Fraction
from itertools import groupby

from .core import ShopError


def exposure(store, source_lot):
    qty = defaultdict(int)
    stock = defaultdict(Fraction)
    sold = defaultdict(Fraction)
    sale_qty = {}
    exits = defaultdict(Fraction)
    product_kind = {r['id']: r['kind'] for r in store.all('SELECT id,kind FROM products')}
    lot_kind = {r['id']: product_kind[r['product_id']] for r in store.all('SELECT id,product_id FROM lots')}
    weights = {r['lot_id']: r['weight'] for r in store.all('SELECT * FROM production_cost_weights')}
    allocations_by_op = defaultdict(list)
    for r in store.all('''SELECT a.*,s.op_id FROM sale_allocations a JOIN sale_lines l ON l.id=a.sale_line_id
            JOIN sales s ON s.id=l.sale_id ORDER BY a.sale_line_id,a.lot_id,a.location_id'''):
        allocations_by_op[r['op_id']].append(r)
    lines_by_op = defaultdict(list)
    for r in store.all('SELECT l.id,l.qty,s.op_id FROM sale_lines l JOIN sales s ON s.id=l.sale_id'):
        lines_by_op[r['op_id']].append(r)
    productions = {r['op_id']: r for r in store.all('SELECT * FROM productions')}
    returns = {r['lot_id']: r for r in store.all('SELECT * FROM return_lines')}
    entries = store.all('SELECT * FROM stock_entries ORDER BY id')
    seeded = False
    for op, group in groupby(entries, key=lambda e: e['op_id']):
        rows = list(group)
        removed = defaultdict(Fraction)
        for e in rows:
            k = (e['lot_id'], e['location_id'])
            if e['qty'] >= 0:
                continue
            n = -e['qty']
            if qty[k] < n:
                raise ShopError('cost_trace_incomplete')
            part = stock[k] * Fraction(n, qty[k])
            stock[k] -= part
            qty[k] -= n
            removed[e['kind'], e['lot_id'], e['location_id']] += part
        # Allocate a sale's outgoing exposure to its actual immutable line/lot
        # allocations. This also handles several lines of the same product.
        allocations = allocations_by_op[op]
        totals = defaultdict(int)
        for a in allocations:
            totals[a['lot_id'], a['location_id']] += a['qty']
        for a in allocations:
            k = a['lot_id'], a['location_id']
            sold[a['sale_line_id']] += removed[('sale',) + k] * Fraction(a['qty'], totals[k])
        for r in lines_by_op[op]:
            sale_qty[r['id']] = r['qty']
        production = productions.get(op)
        production_part = Fraction()
        if production:
            for (kind, lot, loc), part in list(removed.items()):
                if kind == 'production_in':
                    loss = (part * Fraction(production['abnormal_loss_g'], production['food_in_g'])
                            if lot_kind[lot] != 'packaging' else Fraction())
                    exits['loss', lot, loc] += loss
                    production_part += part - loss
        outputs = [e for e in rows if e['kind'] == 'production_out']
        if production_part and any(e['lot_id'] not in weights for e in outputs):
            # An old zero-cost/value-weighted operation has no recoverable weights.
            # Refuse rather than invent the missing accounting evidence.
            raise ShopError('cost_trace_incomplete')
        output_weight = sum(weights.get(e['lot_id'], 0) for e in outputs)
        for e in rows:
            if e['qty'] <= 0:
                continue
            k = e['lot_id'], e['location_id']
            kind = e['kind']
            part = Fraction()
            if kind == 'receipt' and e['lot_id'] == source_lot and not seeded:
                part, seeded = Fraction(1), True
            elif kind == 'transfer_in':
                part = sum((v for (t, lot, _), v in removed.items() if t == 'transfer_out' and lot == e['lot_id']), Fraction())
            elif kind == 'thaw_in':
                part = sum((v for (t, _, _), v in removed.items() if t == 'thaw_out'), Fraction())
            elif kind == 'production_out' and production_part:
                part = production_part * Fraction(weights[e['lot_id']], output_weight)
            elif kind == 'return':
                r = returns.get(e['lot_id'])
                if r:
                    line = r['sale_line_id']
                    part = sold[line] * Fraction(r['qty'], sale_qty[line])
                    sold[line] -= part
                    sale_qty[line] -= r['qty']
            # A positive count has no receipt evidence. It creates quantity, not
            # another claim on this receipt's price correction.
            qty[k] += e['qty']
            stock[k] += part
        for (kind, lot, loc), part in removed.items():
            if kind not in ('sale', 'production_in', 'transfer_out', 'thaw_out'):
                exits['supplier_return' if kind == 'supplier_return' else 'loss', lot, loc] += part
    result = [('stock', lot, loc, 0, part) for (lot, loc), part in stock.items() if part]
    result += [('sale', 0, 0, line, part) for line, part in sold.items() if part]
    result += [(kind, lot, loc, 0, part) for (kind, lot, loc), part in exits.items() if part]
    if not seeded or sum((r[-1] for r in result), Fraction()) != 1:
        raise ShopError('cost_trace_incomplete')
    return sorted(result, key=lambda r: r[:4])


def split(amount, rows):
    """Largest remainder, with stable tie-breaking; exact and sign-symmetric."""
    total = abs(amount)
    exact = [total * r[-1] for r in rows]
    parts = [x.numerator // x.denominator for x in exact]
    order = sorted(range(len(rows)), key=lambda i: (-(exact[i] - parts[i]), i))
    for i in order[:total - sum(parts)]:
        parts[i] += 1
    return [p if amount >= 0 else -p for p in parts]
