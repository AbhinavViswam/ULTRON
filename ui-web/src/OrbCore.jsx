import React, { useEffect, useState } from 'react';

const OrbCore = ({ state, audioLevel = 0 }) => {
  const [rings, setRings] = useState([]);

  // Generate concentric rings while listening if audio spikes
  useEffect(() => {
    if (state === 'listening' && audioLevel > 0.22 && rings.length < 4) {
      // Add a new ring if the last one has expanded enough (or if empty)
      setRings(prev => {
        if (prev.length === 0 || prev[prev.length - 1].progress > 0.25) {
          return [...prev, { id: Date.now(), progress: 0.0, opacity: 0.55 }];
        }
        return prev;
      });
    }
  }, [audioLevel, state]);

  // Animate listening rings
  useEffect(() => {
    if (rings.length === 0) return;
    
    let animationFrameId;
    const animateRings = () => {
      setRings(prev => 
        prev
          .map(r => ({
            ...r,
            progress: r.progress + 0.022,
            opacity: r.opacity - 0.013
          }))
          .filter(r => r.opacity > 0)
      );
      animationFrameId = requestAnimationFrame(animateRings);
    };
    
    // throttle animation to roughly 30fps to match the python GUI
    const intervalId = setInterval(() => {
      animationFrameId = requestAnimationFrame(animateRings);
    }, 33);

    return () => {
      clearInterval(intervalId);
      if (animationFrameId) cancelAnimationFrame(animationFrameId);
    };
  }, [rings.length]);

  // Clear rings when not listening
  useEffect(() => {
    if (state !== 'listening') setRings([]);
  }, [state]);

  const swell = state === 'speaking' ? audioLevel * 0.22 : 0;
  const coreScale = 1 + swell;
  
  // Base colors for states (using orange theme)
  const isTool = state === 'tool';
  const isThinking = state === 'thinking';
  const isListening = state === 'listening';
  const isSpeaking = state === 'speaking';
  const isIdle = state === 'idle' || !state;

  return (
    <div className="relative flex items-center justify-center w-80 h-80">
      
      {/* Deep Ambient Glow */}
      <div 
        className="absolute inset-0 rounded-full blur-[60px] transition-all duration-75"
        style={{
          background: isTool ? '#ea580c' : isThinking ? '#fbbf24' : '#f97316',
          opacity: isIdle ? 0.2 : 0.4 + (audioLevel * 0.4),
          transform: `scale(${1 + (audioLevel * 0.5)})`
        }} 
      />
      
      {/* Dynamic Listening Rings */}
      {rings.map(ring => {
        const size = 176 + (ring.progress * 150); // scales outward from 176px
        return (
          <div 
            key={ring.id}
            className="absolute rounded-full border border-orange-500"
            style={{
              width: `${size}px`,
              height: `${size}px`,
              opacity: ring.opacity,
              transition: 'none'
            }}
          />
        );
      })}

      {/* SVG Layer for Sweeps and Orbits */}
      <svg className="absolute inset-0 w-full h-full overflow-visible z-20 pointer-events-none">
        
        {/* Tool Sweeping Arc */}
        {isTool && (
          <g className="animate-[spin_2s_linear_infinite] origin-center">
            {/* The sweeping arc using SVG stroke dash */}
            <circle 
              cx="160" cy="160" r="92" 
              fill="none" 
              stroke="url(#sweepGrad)" 
              strokeWidth="6" 
              strokeLinecap="round"
              strokeDasharray="100 600"
            />
          </g>
        )}

        {/* Thinking Orbiting Dot */}
        {isThinking && (
          <g className="animate-[spin_1.5s_linear_infinite] origin-center">
            {/* Faint orbit track */}
            <circle cx="160" cy="160" r="92" fill="none" stroke="#fbbf24" strokeWidth="2" strokeOpacity="0.2" />
            {/* The orbiting dot */}
            <circle cx="252" cy="160" r="4" fill="#fbbf24" />
          </g>
        )}

        {/* Gradients */}
        <defs>
          <linearGradient id="sweepGrad" x1="100%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor="#ea580c" stopOpacity="1" />
            <stop offset="100%" stopColor="#ea580c" stopOpacity="0" />
          </linearGradient>
        </defs>
      </svg>

      {/* The 3D Core Sphere */}
      <div 
        className={`relative rounded-full flex items-center justify-center overflow-hidden transition-all duration-75 z-10 ${
          isIdle ? 'animate-[breathe_8s_ease-in-out_infinite]' : ''
        }`}
        style={{
          width: '176px', // 88px diameter * 2 to match gui scaling somewhat
          height: '176px',
          transform: `scale(${coreScale})`,
          background: isThinking
            ? 'radial-gradient(circle at 35% 35%, #fef3c7, #fbbf24 40%, #b45309 80%, #451a03 100%)' 
            : isTool
            ? 'radial-gradient(circle at 35% 35%, #fdba74, #ea580c 40%, #9a3412 80%, #431407 100%)'
            : 'radial-gradient(circle at 35% 35%, #fed7aa, #f97316 40%, #9a3412 80%, #431407 100%)',
          boxShadow: 'inset -10px -10px 30px rgba(0,0,0,0.8), inset 5px 5px 20px rgba(255,255,255,0.4), 0 0 15px rgba(0,0,0,0.5)'
        }}
      >
        {/* Core highlight for depth */}
        <div className="absolute top-1/3 left-1/3 -translate-x-1/2 -translate-y-1/2 w-16 h-16 bg-white rounded-full blur-[15px] opacity-40" />
      </div>

    </div>
  );
};

export default OrbCore;
