import React, { useState } from 'react';
import './Register.css';

const Register = () => {
  const [formData, setFormData] = useState({
    email: '',
    password: ''
  });
  const [message, setMessage] = useState({ text: '', type: '' });
  const [apiKey, setApiKey] = useState(null);
  const [loading, setLoading] = useState(false);

  const handleChange = (e) => {
    setFormData({
      ...formData,
      [e.target.name]: e.target.value
    });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setMessage({ text: '', type: '' });

    try {
      const response = await fetch('/register', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(formData)
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || 'Registration failed');
      }

      setMessage({ text: 'Registration successful! Save your API key securely.', type: 'success' });
      setApiKey(data.api_key);
      // Clear form but keep API key visible
      setFormData({ email: '', password: '' });
    } catch (err) {
      setMessage({ text: err.message, type: 'error' });
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className=\"register-page\">
      <div className=\"register-container\">
        <div className=\"register-header\">
          <h1>Create Your Account</h1>
          <p>Get started with Agent Gateway by registering your account</p>
        </div>
        
        {message.text && (
          <div className={\lert alert-\\}>
            {message.text}
          </div>
        )}
        
        <form onSubmit={handleSubmit} className=\"register-form\">
          <div className=\"form-group\">
            <label htmlFor=\"email\">Email Address</label>
            <input
              type=\"email\"
              id=\"email\"
              name=\"email\"
              value={formData.email}
              onChange={handleChange}
              required
              placeholder=\"you@example.com\"
            />
          </div>
          
          <div className=\"form-group\">
            <label htmlFor=\"password\">Password</label>
            <input
              type=\"password\"
              id=\"password\"
              name=\"password\"
              value={formData.password}
              onChange={handleChange}
              required
              placeholder=\"Choose a password\"
            />
          </div>
          
          <button type=\"submit\" className=\"submit-btn\" disabled={loading}>
            {loading ? 'Registering...' : 'Register'}
          </button>
        </form>
        
        {apiKey && (
          <div className=\"api-key-section\">
            <h2>Your API Key</h2>
            <p>Save this key securely - it will not be shown again:</p>
            <div className=\"api-key-display\">
              {apiKey}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default Register;
