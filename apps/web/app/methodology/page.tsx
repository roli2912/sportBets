import Link from "next/link";

export const metadata = {
  title: "Methodology — how picks are made and graded",
  description:
    "How the +EV engine works, how picks are published immutably, and how every result is graded against the closing line. Educational content only.",
};

export default function MethodologyPage() {
  return (
    <>
      <h1>Methodology</h1>
      <p className="sub">
        What the picks are, where they come from, and how every one of them is
        graded in public. Educational and analytical content only — this is
        research, not betting advice.
      </p>

      <h2>What a pick is (and is not)</h2>
      <p>
        A pick is a documented market observation: at a specific timestamp, a
        specific bookmaker offered a price that our analysis considered higher
        than the fair probability implied by the sharpest available market. It
        is an educational record of that disagreement — not an instruction to
        bet, and never a promise of profit. Outcomes are always uncertain, and
        a positive expected value is a statistical statement about the long
        run, not about any single bet.
      </p>

      <h2>Two kinds of signals, clearly labeled</h2>
      <ul>
        <li>
          <strong>Market-edge signals</strong> come from the +EV engine: we
          remove the bookmaker margin (de-vig) from a sharp reference price,
          derive a fair probability, and flag soft-bookmaker prices whose
          expected value clears a pre-set threshold. These are labeled as
          market signals with validation ongoing.
        </li>
        <li>
          <strong>Verified model picks</strong> come from per-sport predictive
          models. A model earns the &ldquo;verified&rdquo; badge only after
          passing pre-registered statistical gates (minimum sample size and a
          closing-line-value confidence interval excluding zero) that were
          written down before any results were seen — and that never change
          after the fact.
        </li>
      </ul>

      <h2>Immutability: the record cannot be edited</h2>
      <p>
        Every pick is frozen at publication — timestamp, market, outcome, exact
        odds, bookmaker, and suggested stake. The database physically rejects
        updates and deletions of picks. Losses stay on the record forever,
        next to the wins and the pushes.
      </p>

      <h2>Grading: closing line value, not win rate</h2>
      <p>
        Every pick is graded twice. First against the final result
        (win/loss/push). Second — and more importantly — against the closing
        line: the no-vig price of the sharpest market at kickoff. Beating the
        close consistently (positive CLV) is the strongest available evidence
        that an approach has genuine signal; short-term win rate at a small
        sample size is mostly noise. Our public record always reports sample
        size, CLV distribution, and flat-stake return — never a headline win
        percentage on its own.
      </p>

      <h2>Where the data comes from</h2>
      <p>
        Odds are captured continuously from licensed data providers, including
        sharp reference books. Fixtures, results, and statistics come from
        dedicated sports-data providers. The platform never places bets and
        never handles wagers of any kind.
      </p>

      <p>
        See the full, unedited history — including every loss — on the{" "}
        <Link href="/track-record">track record</Link> page.
      </p>
    </>
  );
}
