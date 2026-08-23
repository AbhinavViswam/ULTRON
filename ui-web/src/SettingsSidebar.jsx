import React, { useState, useEffect } from 'react';

const SettingsSidebar = ({ isOpen, onClose }) => {
  const [settings, setSettings] = useState(null);
  const [keys, setKeys] = useState({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (isOpen && !settings) {
      loadSettings();
    }
  }, [isOpen]);

  const loadSettings = async () => {
    setLoading(true);
    try {
      const res = await fetch(`http://${window.location.hostname}:8000/api/settings`);
      const data = await res.json();
      setSettings(data.settings);
      setKeys(data.keys);
    } catch (err) {
      console.error('Failed to load settings', err);
    }
    setLoading(false);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await fetch(`http://${window.location.hostname}:8000/api/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings, keys })
      });
      onClose();
    } catch (err) {
      console.error('Failed to save settings', err);
    }
    setSaving(false);
  };

  const handleCheckbox = (key, value) => {
    setSettings({ ...settings, [key]: value });
  };

  const getSetting = (key, defaultVal) => {
    if (!settings) return defaultVal;
    if (settings[key] !== undefined) return settings[key];
    const parts = key.split('.');
    let val = settings;
    for (const p of parts) {
      if (val === undefined || val === null) return defaultVal;
      val = val[p];
    }
    return val !== undefined ? val : defaultVal;
  };

  const providerOptions = [
    { label: 'OpenRouter', value: 'openrouterapi' },
    { label: 'Gemini', value: 'geminiapi' },
    { label: 'Groq', value: 'groqapi' },
    { label: 'Local (Ollama)', value: 'localapi' }
  ];

  return (
    <div className={`fixed right-0 top-0 bottom-0 w-80 glass-panel border-l border-slate-700/50 transform transition-transform duration-300 z-50 flex flex-col ${
      isOpen ? 'translate-x-0' : 'translate-x-full'
    }`}>
      <div className="p-4 border-b border-slate-700/50 flex justify-between items-center bg-slate-900/40">
        <h2 className="text-lg font-bold text-white">Settings</h2>
        <button onClick={onClose} className="text-slate-400 hover:text-white">
          <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-8">
        {loading || !settings ? (
          <div className="text-slate-400 text-center mt-10">Loading...</div>
        ) : (
          <>
            {/* Providers Section */}
            <div className="space-y-4">
              <h3 className="text-sm font-semibold text-orange-400 uppercase tracking-wider">Providers</h3>
              
              <div>
                <label className="block text-xs text-slate-400 mb-1">AI Provider</label>
                <select 
                  value={settings.active_provider || 'openrouterapi'}
                  onChange={e => setSettings({...settings, active_provider: e.target.value})}
                  className="w-full bg-slate-800 border border-slate-700 rounded p-2 text-white text-sm"
                >
                  {providerOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>

              <div>
                <label className="block text-xs text-slate-400 mb-1">Vision Provider</label>
                <select 
                  value={settings.vision_provider || 'openrouterapi'}
                  onChange={e => setSettings({...settings, vision_provider: e.target.value})}
                  className="w-full bg-slate-800 border border-slate-700 rounded p-2 text-white text-sm"
                >
                  {providerOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
            </div>

            {/* Models Section */}
            <div className="space-y-4 border-t border-slate-700/50 pt-4">
              <h3 className="text-sm font-semibold text-orange-400 uppercase tracking-wider">Models</h3>
              
              <div>
                <label className="block text-xs text-slate-400 mb-1">OpenRouter Model</label>
                <input type="text" value={settings.openrouter_model || ''} onChange={e => setSettings({...settings, openrouter_model: e.target.value})} className="w-full bg-slate-800 border border-slate-700 rounded p-2 text-white text-sm" />
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1">Groq Model</label>
                <input type="text" value={settings.groq_model || ''} onChange={e => setSettings({...settings, groq_model: e.target.value})} className="w-full bg-slate-800 border border-slate-700 rounded p-2 text-white text-sm" />
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1">Vision Model</label>
                <input type="text" value={settings.vision_model || ''} onChange={e => setSettings({...settings, vision_model: e.target.value})} className="w-full bg-slate-800 border border-slate-700 rounded p-2 text-white text-sm" />
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1">Local Model</label>
                <input type="text" value={settings.local_model || ''} onChange={e => setSettings({...settings, local_model: e.target.value})} className="w-full bg-slate-800 border border-slate-700 rounded p-2 text-white text-sm" />
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1">Local API URL</label>
                <input type="text" value={settings.local_api_url || 'http://localhost:11434/v1'} onChange={e => setSettings({...settings, local_api_url: e.target.value})} className="w-full bg-slate-800 border border-slate-700 rounded p-2 text-white text-sm" />
              </div>
            </div>

            {/* API Keys */}
            <div className="space-y-4 border-t border-slate-700/50 pt-4">
              <h3 className="text-sm font-semibold text-orange-400 uppercase tracking-wider">API Keys</h3>
              
              <div>
                <label className="block text-xs text-slate-400 mb-1">OpenRouter Key</label>
                <input type="password" value={keys.openrouter || ''} onChange={e => setKeys({...keys, openrouter: e.target.value})} className="w-full bg-slate-800 border border-slate-700 rounded p-2 text-white text-sm" placeholder="sk-or-v1-..." />
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1">Groq Key</label>
                <input type="password" value={keys.groq || ''} onChange={e => setKeys({...keys, groq: e.target.value})} className="w-full bg-slate-800 border border-slate-700 rounded p-2 text-white text-sm" placeholder="gsk_..." />
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1">Gemini Key (Google)</label>
                <input type="password" value={keys.google || ''} onChange={e => setKeys({...keys, google: e.target.value})} className="w-full bg-slate-800 border border-slate-700 rounded p-2 text-white text-sm" placeholder="AIzaSy..." />
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1">Vision API Key</label>
                <input type="password" value={keys.vision || ''} onChange={e => setKeys({...keys, vision: e.target.value})} className="w-full bg-slate-800 border border-slate-700 rounded p-2 text-white text-sm" placeholder="sk-..." />
              </div>
            </div>

            {/* Assistant Behavior */}
            <div className="space-y-4 border-t border-slate-700/50 pt-4">
              <h3 className="text-sm font-semibold text-orange-400 uppercase tracking-wider">Assistant</h3>
              
              <label className="flex items-center space-x-2 text-sm text-slate-200">
                <input type="checkbox" checked={!!settings.truth_mode} onChange={e => handleCheckbox('truth_mode', e.target.checked)} className="rounded bg-slate-800 border-slate-700 text-primary focus:ring-primary" />
                <span>Truth mode (never guess)</span>
              </label>

              <label className="flex items-center space-x-2 text-sm text-slate-200">
                <input type="checkbox" checked={!!settings.self_hearing_guard} onChange={e => handleCheckbox('self_hearing_guard', e.target.checked)} className="rounded bg-slate-800 border-slate-700 text-primary focus:ring-primary" />
                <span title="Ignore own voice via speakers">Self-hearing guard</span>
              </label>

              <label className="flex items-center space-x-2 text-sm text-slate-200">
                <input type="checkbox" checked={!!settings.live_screen} onChange={e => handleCheckbox('live_screen', e.target.checked)} className="rounded bg-slate-800 border-slate-700 text-primary focus:ring-primary" />
                <span>Live screen awareness</span>
              </label>
            </div>

            {/* Idle Chatter */}
            <div className="space-y-4 border-t border-slate-700/50 pt-4 pb-12">
              <h3 className="text-sm font-semibold text-orange-400 uppercase tracking-wider">Idle Chat</h3>
              
              <label className="flex items-center space-x-2 text-sm text-slate-200">
                <input type="checkbox" checked={!!getSetting('idle_chat.enabled', true)} onChange={e => handleCheckbox('idle_chat.enabled', e.target.checked)} className="rounded bg-slate-800 border-slate-700 text-primary focus:ring-primary" />
                <span>Enable idle chat</span>
              </label>

              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-[10px] text-slate-400 mb-1">After (mins)</label>
                  <input type="number" value={getSetting('idle_chat.after_minutes', 25)} onChange={e => setSettings({...settings, 'idle_chat.after_minutes': Number(e.target.value)})} className="w-full bg-slate-800 border border-slate-700 rounded p-1.5 text-white text-xs" />
                </div>
                <div>
                  <label className="block text-[10px] text-slate-400 mb-1">Give up (mins)</label>
                  <input type="number" value={getSetting('idle_chat.give_up_after', 0)} onChange={e => setSettings({...settings, 'idle_chat.give_up_after': Number(e.target.value)})} className="w-full bg-slate-800 border border-slate-700 rounded p-1.5 text-white text-xs" />
                </div>
                <div>
                  <label className="block text-[10px] text-slate-400 mb-1">Quiet Start Hr</label>
                  <input type="number" value={getSetting('idle_chat.quiet_start_hour', 22)} onChange={e => setSettings({...settings, 'idle_chat.quiet_start_hour': Number(e.target.value)})} className="w-full bg-slate-800 border border-slate-700 rounded p-1.5 text-white text-xs" />
                </div>
                <div>
                  <label className="block text-[10px] text-slate-400 mb-1">Quiet End Hr</label>
                  <input type="number" value={getSetting('idle_chat.quiet_end_hour', 8)} onChange={e => setSettings({...settings, 'idle_chat.quiet_end_hour': Number(e.target.value)})} className="w-full bg-slate-800 border border-slate-700 rounded p-1.5 text-white text-xs" />
                </div>
              </div>

              <label className="flex items-center space-x-2 text-xs text-slate-300">
                <input type="checkbox" checked={!!getSetting('idle_chat.silent_when_mic_off', false)} onChange={e => handleCheckbox('idle_chat.silent_when_mic_off', e.target.checked)} className="rounded bg-slate-800 border-slate-700 text-primary focus:ring-primary" />
                <span>Silent when mic off</span>
              </label>

              <label className="flex items-center space-x-2 text-xs text-slate-300">
                <input type="checkbox" checked={!!getSetting('idle_chat.silent_in_fullscreen', false)} onChange={e => handleCheckbox('idle_chat.silent_in_fullscreen', e.target.checked)} className="rounded bg-slate-800 border-slate-700 text-primary focus:ring-primary" />
                <span>Silent in fullscreen apps</span>
              </label>
            </div>
          </>
        )}
      </div>

      <div className="p-4 border-t border-slate-700/50 bg-slate-900/40 shrink-0">
        <button 
          onClick={handleSave}
          disabled={saving || loading}
          className="w-full py-2 bg-primary hover:bg-primary/90 text-white rounded-lg font-medium transition-colors"
        >
          {saving ? 'Saving...' : 'Save & Reload'}
        </button>
      </div>
    </div>
  );
};

export default SettingsSidebar;
