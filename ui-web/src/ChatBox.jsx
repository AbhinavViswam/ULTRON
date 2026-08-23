import { useEffect, useRef } from 'react';

export default function ChatBox({ messages }) {
  const endRef = useRef(null);

  // Auto-scroll to bottom on new message
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-slate-500 fade-in-up">
        <svg className="w-16 h-16 mb-4 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
        </svg>
        <p className="text-lg font-medium">No messages yet.</p>
        <p className="text-sm">Say hello to get started!</p>
      </div>
    );
  }

  return (
    <div className="space-y-6 flex flex-col">
      {messages.map((msg, idx) => {
        const isUser = msg.role === 'user';
        const isSystem = msg.role === 'system';

        if (isSystem) {
          return (
            <div key={msg.id || idx} className="flex justify-center fade-in-up">
              <span className="px-4 py-1 text-xs font-semibold uppercase tracking-wider text-slate-400 bg-slate-800/50 rounded-full border border-slate-700/50">
                {msg.text}
              </span>
            </div>
          );
        }

        return (
          <div 
            key={msg.id || idx} 
            className={`flex flex-col max-w-[80%] fade-in-up ${isUser ? 'self-end items-end' : 'self-start items-start'}`}
          >
            {/* Bubble */}
            <div 
              className={`px-5 py-3 rounded-2xl shadow-md backdrop-blur-sm whitespace-pre-wrap ${
                isUser 
                  ? 'bg-primary/90 text-white rounded-tr-sm' 
                  : 'bg-slate-800/80 text-slate-200 border border-slate-700/50 rounded-tl-sm'
              }`}
            >
              {msg.text}
            </div>
            
            {/* Metadata (source) */}
            {(msg.source || msg.queued) && (
              <div className="flex items-center space-x-2 text-[10px] text-slate-500 mt-1 uppercase tracking-wide font-semibold px-1">
                {msg.source && <span>Via {msg.source}</span>}
                {msg.queued && <span className="bg-orange-500/20 text-orange-400 px-1.5 py-0.5 rounded">Queued</span>}
              </div>
            )}
          </div>
        );
      })}
      <div ref={endRef} />
    </div>
  );
}
