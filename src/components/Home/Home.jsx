import React from 'react';
import './Home.css';

const Home = () => {
  return (
    <div className="home-page">
      <header className="home-header">
        <h1>Welcome to Agent Gateway</h1>
        <p>Register, configure, and deploy AI agents with ease</p>
      </header>
      <main className="home-content">
        <section className="features">
          <h2>Features</h2>
          <div className="feature-list">
            <div className="feature-item">
              <h3>Easy Registration</h3>
              <p>Create your account and get started in minutes</p>
            </div>
            <div className="feature-item">
              <h3>Agent Configuration</h3>
              <p>Configure your AI agents with custom prompts and tools</p>
            </div>
            <div className="feature-item">
              <h3>Simple API</h3>
              <p>Call your agents with a single API endpoint</p>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
};

export default Home;
