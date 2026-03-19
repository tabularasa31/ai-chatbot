import { Upload, Clock, Mail, Brain } from 'lucide-react';
import { motion } from 'framer-motion';
import { useScrollAnimation } from '../hooks/useScrollAnimation';

interface FeatureCardProps {
  icon: React.ReactNode;
  title: string;
  description: string;
  index: number;
}

function FeatureCard({ icon, title, description, index }: FeatureCardProps) {
  const [ref, isInView] = useScrollAnimation();

  return (
    <motion.div
      ref={ref}
      initial={{ opacity: 0, y: 20 }}
      animate={isInView ? { opacity: 1, y: 0 } : { opacity: 0, y: 20 }}
      transition={{ duration: 0.5, delay: index * 0.1 }}
      className="bg-[#12121A] backdrop-blur-sm border border-[#1E1E2E] rounded-xl p-8 hover:bg-[#1a1a24] hover:scale-105 transition-all"
    >
      <div className="text-[#38BDF8] mb-4">{icon}</div>
      <h3 className="text-[#FAF5FF] text-xl mb-2">{title}</h3>
      <p className="text-[#FAF5FF]/60">{description}</p>
    </motion.div>
  );
}

export function Features() {
  const features = [
    {
      icon: <Upload size={32} />,
      title: 'Upload',
      description: 'Load docs in 2 minutes',
    },
    {
      icon: <Clock size={32} />,
      title: 'Works 24/7',
      description: 'Always available for your customers',
    },
    {
      icon: <Mail size={32} />,
      title: 'Daily reports',
      description: 'Get insights delivered to your inbox',
    },
    {
      icon: <Brain size={32} />,
      title: 'Understands context',
      description: 'Smart responses that make sense',
    },
  ];

  return (
    <section id="features" className="max-w-7xl mx-auto px-6 py-20">
      <div className="grid md:grid-cols-2 gap-6">
        {features.map((feature, index) => (
          <FeatureCard
            key={index}
            icon={feature.icon}
            title={feature.title}
            description={feature.description}
            index={index}
          />
        ))}
      </div>
    </section>
  );
}