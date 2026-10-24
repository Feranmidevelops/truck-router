import './HowItWorks.css'

const STEPS = [
  {
    tag: 'Routes',
    title: 'Three truck routes',
    text: "TomTom plans the trip in truck mode with your load's weight. When it finds fewer than three distinct routes, detours through points either side of the direct line fill the gap.",
  },
  {
    tag: 'Checkpoints',
    title: 'Weather when you get there',
    text: "A checkpoint every 10, 25 or 50 miles. Each one reads the hourly forecast for the time the truck reaches it, worked out from TomTom's own timing along the road.",
  },
  {
    tag: 'Risk',
    title: 'Five levels, fixed thresholds',
    text: 'Wind, rain and snow are each scored from Low to No Travel. Heavy loads raise the wind level: over 30,000 lb in 45 mph wind is No Travel.',
  },
  {
    tag: 'Ranking',
    title: 'Safest route first',
    text: 'Fewest No Travel miles, then Severe, then High, then the lowest average risk. Travel time only settles a tie.',
  },
]

export function HowItWorks() {
  return (
    <section className="how" id="how-it-works" aria-labelledby="how-title">
      <div className="how-head">
        <h2 id="how-title" className="how-title display">
          How it works
        </h2>
        <p className="how-lede">From a typed trip to a recommended route, in four steps.</p>
        <TruckLineArt />
      </div>
      <ol className="how-steps">
        {STEPS.map((s, i) => (
          <li key={s.tag} className="how-step">
            <span className="chip">{s.tag}</span>
            <span className="how-step-number display" aria-hidden="true">
              {String(i + 1).padStart(2, '0')}
            </span>
            <h3 className="how-step-title">{s.title}</h3>
            <p className="how-step-text">{s.text}</p>
          </li>
        ))}
      </ol>
    </section>
  )
}

// A tractor-trailer in outline on a dashed road, with a checkpoint every few lengths
function TruckLineArt() {
  return (
    <svg className="how-art" viewBox="0 0 320 96" aria-hidden="true" focusable="false">
      <g fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" vectorEffect="non-scaling-stroke">
        <path d="M14 14h182v48H14z" />
        <path d="M30 62v6M180 62v6M196 56h10" />
        <path d="M206 64V32q0-8 8-10h26l16 16h8q6 0 6 6v20h-8" />
        <path d="M220 28h18l11 11h-29z" />
        <path d="M206 64h10M246 64h10" />
        <circle cx="40" cy="70" r="8" />
        <circle cx="60" cy="70" r="8" />
        <circle cx="200" cy="70" r="8" />
        <circle cx="226" cy="70" r="8" />
        <circle cx="262" cy="70" r="8" />
        <path d="M0 86h320" strokeDasharray="6 6" />
      </g>
      <g fill="currentColor">
        <circle cx="24" cy="86" r="3" />
        <circle cx="120" cy="86" r="3" />
        <circle cx="216" cy="86" r="3" />
        <circle cx="312" cy="86" r="3" />
      </g>
    </svg>
  )
}
