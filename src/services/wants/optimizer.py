"""
Buy a wants list for the least money: card prices plus each seller's
shipping, paid once per seller.

Greedy, version 1:
  1. Fill each item (scarcest first) from the offer that adds the least cost:
     its price, plus the seller's shipping if nothing is bought from them yet.
  2. Drop sellers: move everything bought from one seller to sellers already
     in the plan, and keep the move if the total goes down. Repeat until no
     move helps.
  3. Repeat both steps once per seller who sells several of the items,
     counting that seller's shipping as already paid in step 1, so plans built
     around one seller (cheaper overall, though no single card is cheapest
     there) get considered. Keep the cheapest plan.
It isn't guaranteed to find the cheapest plan; an integer program (e.g.
OR-Tools) would, if the savings ever justify it.

Pure Python over plain data, so it can be tested without a database.
"""
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

# How many sellers step 3 builds plans around (those selling the most items); bounds the work.
MAX_SEEDS = 20


@dataclass(frozen=True)
class Offer:
    listing_id: uuid.UUID
    seller_id: uuid.UUID
    price_cents: int


@dataclass
class Plan:
    # want item → listing → copies
    picks: dict[uuid.UUID, dict[uuid.UUID, int]] = field(default_factory=dict)
    # want item → copies no offer could cover
    missing: dict[uuid.UUID, int] = field(default_factory=dict)


class _State:
    def __init__(self, offers: dict[uuid.UUID, list[Offer]], stock: dict[uuid.UUID, int],
                 shipping: dict[uuid.UUID, int], seed: uuid.UUID | None = None):
        self.offers, self.shipping, self.seed = offers, shipping, seed
        self.stock = dict(stock)
        self.picks: dict[uuid.UUID, dict[uuid.UUID, int]] = defaultdict(dict)
        self.seller_copies: dict[uuid.UUID, int] = defaultdict(int)
        self.by_listing = {o.listing_id: o for item_offers in offers.values() for o in item_offers}

    def copy(self) -> "_State":
        other = _State.__new__(_State)
        other.offers, other.shipping, other.by_listing = self.offers, self.shipping, self.by_listing
        other.seed = self.seed
        other.stock = dict(self.stock)
        other.picks = defaultdict(dict, {item: dict(p) for item, p in self.picks.items()})
        other.seller_copies = defaultdict(int, self.seller_copies)
        return other

    def cost(self) -> int:
        prices = sum(self.by_listing[listing].price_cents * n for p in self.picks.values() for listing, n in p.items())
        return prices + sum(self.shipping[s] for s, n in self.seller_copies.items() if n)

    def take(self, item: uuid.UUID, offer: Offer, copies: int) -> None:
        self.stock[offer.listing_id] -= copies
        self.picks[item][offer.listing_id] = self.picks[item].get(offer.listing_id, 0) + copies
        self.seller_copies[offer.seller_id] += copies

    def fill(self, item: uuid.UUID, copies: int, sellers: set[uuid.UUID] | None = None) -> int:
        """Buy up to copies of item, cheapest added cost first. Returns how many couldn't be bought."""
        while copies:
            usable = [o for o in self.offers.get(item, []) if self.stock[o.listing_id] > 0
                      and (sellers is None or o.seller_id in sellers)]
            if not usable:
                return copies
            best = min(usable, key=lambda o: (
                o.price_cents + (0 if self.seller_copies[o.seller_id] or o.seller_id == self.seed
                                 else self.shipping[o.seller_id]),
                o.price_cents, str(o.listing_id)))
            # The same listing stays the cheapest choice once its seller is in the plan.
            n = min(copies, self.stock[best.listing_id])
            self.take(item, best, n)
            copies -= n
        return 0

    def drop_seller(self, seller: uuid.UUID) -> dict[uuid.UUID, int]:
        """Remove everything bought from seller; returns item → copies to rebuy."""
        rebuy: dict[uuid.UUID, int] = defaultdict(int)
        for item, picks in self.picks.items():
            for listing, n in list(picks.items()):
                if self.by_listing[listing].seller_id == seller:
                    del picks[listing]
                    self.stock[listing] += n
                    rebuy[item] += n
        self.seller_copies[seller] = 0
        return rebuy


def _plan(
    needs: dict[uuid.UUID, int], offers: dict[uuid.UUID, list[Offer]], stock: dict[uuid.UUID, int],
    shipping: dict[uuid.UUID, int], seed: uuid.UUID | None,
) -> tuple[_State, dict[uuid.UUID, int]]:
    state = _State(offers, stock, shipping, seed)
    missing = {}
    # Scarce items first, so common cards don't use up the copies they share.
    for item in sorted(needs, key=lambda i: len(offers.get(i, []))):
        left = state.fill(item, needs[item])
        if left:
            missing[item] = left
    state.seed = None

    improved = True
    while improved:
        improved = False
        # Sellers with the fewest copies first: the cheapest to replace.
        used = [s for s, n in state.seller_copies.items() if n]
        current = state.cost()
        for seller in sorted(used, key=lambda s: (state.seller_copies[s], str(s))):
            trial = state.copy()
            rebuy = trial.drop_seller(seller)
            others = {s for s, n in trial.seller_copies.items() if n}
            if all(trial.fill(item, n, others) == 0 for item, n in rebuy.items()) and trial.cost() < current:
                state, improved = trial, True
                break
    return state, missing


def optimize(
    needs: dict[uuid.UUID, int], offers: dict[uuid.UUID, list[Offer]], stock: dict[uuid.UUID, int],
    shipping: dict[uuid.UUID, int],
) -> Plan:
    """needs: want item → copies wanted. offers: want item → listings that
    match it (a listing can match several items). stock: listing → copies for
    sale. shipping: seller → cheapest shipping; sellers missing here are skipped."""
    offers = {item: [o for o in item_offers if o.seller_id in shipping] for item, item_offers in offers.items()}
    items_by_seller: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for item, item_offers in offers.items():
        for o in item_offers:
            items_by_seller[o.seller_id].add(item)
    seeds = sorted((s for s, items in items_by_seller.items() if len(items) > 1),
                   key=lambda s: (-len(items_by_seller[s]), str(s)))[:MAX_SEEDS]

    best: tuple[_State, dict[uuid.UUID, int]] | None = None
    for seed in [None, *seeds]:
        state, missing = _plan(needs, offers, stock, shipping, seed)
        # Fewest uncovered copies first, then the lowest total.
        if best is None or (sum(missing.values()), state.cost()) < (sum(best[1].values()), best[0].cost()):
            best = (state, missing)
    assert best is not None
    state, missing = best
    return Plan(picks={item: dict(p) for item, p in state.picks.items() if p}, missing=missing)
