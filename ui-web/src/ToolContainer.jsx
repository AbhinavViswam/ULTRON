import React from 'react';

const ToolContainer = ({ activeTool, isConnected }) => {
  return (
    <div className="absolute right-8 top-1/2 -translate-y-1/2 w-64">
      {/* Connection string from center to this box */}
      <svg className="absolute -left-[300px] top-1/2 -translate-y-1/2 w-[300px] h-[2px] z-0 overflow-visible pointer-events-none">
        <line 
          x1="0" y1="0" x2="300" y2="0" 
          stroke={activeTool ? "rgba(249, 115, 22, 0.6)" : "transparent"} 
          strokeWidth="2" 
          strokeDasharray="4 4"
          className={activeTool ? "animate-[dash_1s_linear_infinite]" : ""}
        />
        {activeTool && (
          <circle cx="300" cy="0" r="4" fill="rgba(249, 115, 22, 1)" className="animate-ping" />
        )}
      </svg>

      {/* Box */}
      <div className={`relative z-10 glass-panel border p-4 rounded-xl transition-all duration-500 ${
        activeTool 
          ? 'border-orange-500/50 bg-orange-900/20 shadow-[0_0_30px_rgba(249,115,22,0.2)] scale-100 opacity-100' 
          : 'border-slate-700/50 bg-slate-900/40 scale-95 opacity-50'
      }`}>
        <div className="flex items-center gap-3 mb-2">
          <div className={`w-2 h-2 rounded-full ${activeTool ? 'bg-orange-400 animate-pulse' : 'bg-slate-600'}`} />
          <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider">
            System Tools
          </h3>
        </div>
        
        <div className="h-12 flex items-center justify-center">
          {activeTool ? (
            <div className="text-orange-300 font-mono text-sm break-all font-semibold fade-in-up">
              {activeTool}
            </div>
          ) : (
            <div className="text-slate-500 text-xs italic">
              {isConnected ? 'Monitoring...' : 'Offline'}
            </div>
          )}
        </div>
      </div>
      
      <style>{`
        @keyframes dash {
          to { stroke-dashoffset: -8; }
        }
      `}</style>
    </div>
  );
};

export default ToolContainer;
