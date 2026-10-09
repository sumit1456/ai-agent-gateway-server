import React, { useState } from 'react';
import './Login.css';

const Login = () => {
  const [formData, setFormData] = useState({
    email: '',
    password: ''
  });
  const [message, setMessage] = useState({ text: '', type: '' });
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
      const response = await fetch('/login', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(formData)
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || 'Login failed');
      }

      // In a real app, we would store the token and redirect
      // For now, we'll just show a success message
      setMessage({ text: 'Login successful! (In a real app, you would be redirected)', type: 'success' });
      setFormData({ email: '', password: '' });
    } catch (err) {
      setMessage({ text: err.message, type: 'error' });
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className=\"login-page\">
      <div className=\"login-container\">
        <div className=\"login-header\">
          <h1>Sign In</h1>
          <p>Access your Agent Gateway account</p>
        </div>
        
        {message.text && (
          <div className={\lert alert-\\}>
            {message.text}
          </div>
        )}
        
        <form onSubmit={handleSubmit} className=\"login-form\">
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
              placeholder=\"Enter your password\"
            />
          </div>
          
          <button type=\"submit\" className=\"submit-btn\" disabled={loading}>
            {loading ? 'Logging in...' : 'Sign In'}
          </button>
        </form>
        
        <div className=\"login-footer\">
          <p>Don't have an account? <span className=\"register-link\">Register here</span></p>
        </div>
      </div>
    </div>
  );
};

export default Login;
